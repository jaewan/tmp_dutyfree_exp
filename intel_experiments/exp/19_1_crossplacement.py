#!/usr/bin/env python3
"""Phase 19.1 CXL cross-placement matrix."""

import csv
import json
import random
import re
import signal
import statistics
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.1_crossplacement.csv"
REPORT = ROOT / "results" / "processed" / "19.1_phase_report.md"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase_nocap"
AGG = ROOT / "bench" / "aggressor" / "stream_wb"

N_TRIALS = 30
MEASURE_SEC = 10
WSS = 32 * 1024 * 1024
LLC_MB = 60
VICTIM_CPU = 0
LOCAL_CORES = [1]
CXL_CORES = [1, 2]
CALIBRATED_BW = {
    0: 14.65,  # 1 local WB+pf core, median of 5 x 6s calibration runs
    2: 17.12,  # 2 CXL WB+pf cores, median of 5 x 6s calibration runs
}

CORE_EVENTS = "mem_load_retired.l2_hit,mem_load_retired.l3_hit,mem_load_retired.l3_miss,longest_lat_cache.miss"
UNCORE_EVENTS = ",".join([
    "uncore_cha_0/unc_cha_core_snp.evict_one/",
    "uncore_cha_0/unc_cha_core_snp.evict_gtone/",
    "uncore_cha_0/unc_cha_tor_inserts.ia_miss_drd_cxl_exp_local/",
    "uncore_cha_0/unc_cha_tor_occupancy.ia_miss_drd_cxl_exp_local/",
    "uncore_cxlcm/unc_cxlcm_rxc_flits.valid/",
    "uncore_imc_0/cas_count_read/",
    "uncore_imc_1/cas_count_read/",
    "uncore_imc_2/cas_count_read/",
    "uncore_imc_3/cas_count_read/",
])

CELLS = [
    ("QL", None, 0),
    ("QC", None, 2),
    ("AL-VL", 0, 0),
    ("AC-VL", 2, 0),
    ("AL-VC", 0, 2),
    ("AC-VC", 2, 2),
]


def parse_perf(stderr: str) -> dict[str, int]:
    out = {}
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            raw = parts[0].strip()
            event = parts[2].strip().lower().strip("/")
            if raw.startswith("<"):
                out[event] = -1
            else:
                try:
                    out[event] = out.get(event, 0) + int(float(raw))
                except ValueError:
                    pass
    return out


def read_numa(pid: int) -> tuple[str, int, int, int]:
    try:
        text = Path(f"/proc/{pid}/numa_maps").read_text()
    except Exception as e:
        return f"ERR:{type(e).__name__}", 0, 0, 0
    huge = [line for line in text.splitlines() if "huge" in line]
    n0 = sum(int(m.group(1)) for line in huge for m in [re.search(r"N0=(\d+)", line)] if m)
    n2 = sum(int(m.group(1)) for line in huge for m in [re.search(r"N2=(\d+)", line)] if m)
    return " | ".join(huge[:3]), int(n0 > 0), int(n2 > 0), n0 + n2


class AggProc:
    def __init__(self, cpu: int, node: int, duration: int):
        self.cpu = cpu
        self.node = node
        self.duration = duration
        self.proc = None
        self.err = []

    def start(self):
        cmd = ["numactl", f"--membind={self.node}", "--cpunodebind=0", "--",
               str(AGG), "--cpu", str(self.cpu), "--node", str(self.node),
               "--region-gb", "1", "--duration-sec", str(self.duration), "--no-verify"]
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            self.err.append(line.rstrip())

    def bw(self) -> float:
        for line in reversed(self.err):
            m = re.search(r"bw=([0-9.]+) GB/s", line)
            if m:
                return float(m.group(1))
        return 0.0

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def run_perf(events: str, scope: list[str], sec: int) -> subprocess.Popen:
    return subprocess.Popen(["perf", "stat", "-x,", *scope, "-e", events, "--", "sleep", str(sec)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def run_victim(vnode: int):
    cmd = ["numactl", f"--membind={vnode}", "--cpunodebind=0", "--",
           str(VICTIM), "--cpu", str(VICTIM_CPU), "--node", str(vnode),
           "--wss", str(WSS), "--trials", "1", "--run-sec", str(MEASURE_SEC)]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    numa_sample, n0, n2, pages = read_numa(proc.pid)
    core = run_perf(CORE_EVENTS, ["-C", str(VICTIM_CPU)], MEASURE_SEC)
    uncore = run_perf(UNCORE_EVENTS, ["-a"], MEASURE_SEC)
    out, err = proc.communicate(timeout=MEASURE_SEC + 30)
    _, core_err = core.communicate(timeout=MEASURE_SEC + 20)
    _, uncore_err = uncore.communicate(timeout=MEASURE_SEC + 20)
    data = json.loads(out)[0]
    return data, parse_perf(core_err), parse_perf(uncore_err), numa_sample, n0, n2, pages


def run_cell(label: str, agg_node: int | None, victim_node: int) -> list[dict]:
    print(f"=== {label} agg_node={agg_node} victim_node={victim_node}", flush=True)
    duration = N_TRIALS * (MEASURE_SEC + 3) + 60
    aggs = []
    if agg_node is not None:
        cpus = LOCAL_CORES if agg_node == 0 else CXL_CORES
        for cpu in cpus:
            a = AggProc(cpu, agg_node, duration)
            a.start()
            aggs.append(a)
        time.sleep(5)
    rows = []
    try:
        for trial in range(N_TRIALS):
            data, core, uncore, vsample, vn0, vn2, vpages = run_victim(victim_node)
            l2 = max(core.get("mem_load_retired.l2_hit", 0), 0)
            l3h = max(core.get("mem_load_retired.l3_hit", 0), 0)
            l3m = max(core.get("mem_load_retired.l3_miss", 0), 0)
            denom = l3h + l3m
            l3_miss_frac = l3m / denom if denom else 0
            progress_bw = sum(a.bw() for a in aggs)
            bw = 0.0 if agg_node is None else CALIBRATED_BW[agg_node]
            agg_n0 = agg_n2 = 0
            agg_sample = ""
            for a in aggs:
                sample, n0, n2, _ = read_numa(a.proc.pid)
                agg_n0 += n0
                agg_n2 += n2
                if not agg_sample:
                    agg_sample = sample
            row = {
                "cell": label, "trial": trial,
                "victim_node": victim_node, "aggressor_node": -1 if agg_node is None else agg_node,
                "victim_wss_mb": WSS // (1024 * 1024), "llc_capacity_mb": LLC_MB,
                "aggressor_region_gb_per_core": 1, "aggressor_cores": len(aggs),
                "agg_bw_gbps": round(bw, 3),
                "agg_progress_bw_gbps_untrusted": round(progress_bw, 3),
                "cycles_per_load": round(float(data["cycles_per_load"]), 3),
                "tsc_hz": int(data["tsc_hz"]),
                "latency_ns": round(float(data["cycles_per_load"]) / float(data["tsc_hz"]) * 1e9, 2),
                "l2_hit": l2, "l3_hit": l3h, "l3_miss": l3m,
                "l3_miss_fraction": round(l3_miss_frac, 6),
                "longest_lat_cache_miss": max(core.get("longest_lat_cache.miss", 0), 0),
                "sf_evict": max(uncore.get("uncore_cha_0/unc_cha_core_snp.evict_one", 0), 0) + max(uncore.get("uncore_cha_0/unc_cha_core_snp.evict_gtone", 0), 0),
                "cha_cxl_drd": max(uncore.get("uncore_cha_0/unc_cha_tor_inserts.ia_miss_drd_cxl_exp_local", 0), 0),
                "cxl_flits_valid": max(uncore.get("uncore_cxlcm/unc_cxlcm_rxc_flits.valid", 0), 0),
                "imc_cas_rd": sum(max(uncore.get(f"uncore_imc_{i}/cas_count_read", 0), 0) for i in range(4)),
                "victim_numa_n0": vn0, "victim_numa_n2": vn2,
                "aggressor_numa_n0": agg_n0, "aggressor_numa_n2": agg_n2,
                "victim_numa_sample": vsample, "aggressor_numa_sample": agg_sample,
            }
            rows.append(row)
            print(label, trial, row["cycles_per_load"], row["agg_bw_gbps"], row["l3_miss_fraction"], flush=True)
            time.sleep(2)
    finally:
        for a in aggs:
            a.stop()
    return rows


def main():
    random.seed(1901)
    active = [c for c in CELLS]
    random.shuffle(active)
    all_rows = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for cell in active:
        all_rows.extend(run_cell(*cell))
        with OUT.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0]))
            w.writeheader()
            w.writerows(all_rows)
    # Report
    by = {}
    for r in all_rows:
        by.setdefault(r["cell"], []).append(float(r["cycles_per_load"]))
    ql = statistics.mean(by["QL"])
    qc = statistics.mean(by["QC"])
    lines = ["# Phase 19.1 Cross-Placement Report", "", "Victim WSS: 32 MB; LLC capacity: 60 MB. WSS is below 2x LLC capacity because this phase intentionally measures the original LLC-scale victim used for the mechanism discriminator, not CXL idle latency.", "", "| Cell | n | Mean cyc/load | Tax vs placement Q | Mean agg BW GB/s |", "|------|---|---------------|--------------------|------------------|"]
    for cell in ["QL","QC","AL-VL","AC-VL","AL-VC","AC-VC"]:
        rows=[r for r in all_rows if r["cell"]==cell]
        mean=statistics.mean(float(r["cycles_per_load"]) for r in rows)
        bw=statistics.mean(float(r["agg_bw_gbps"]) for r in rows)
        base=ql if cell.endswith("VL") or cell=="QL" else qc
        tax=(mean/base-1)*100 if cell not in ("QL","QC") else 0
        lines.append(f"| {cell} | {len(rows)} | {mean:.2f} | {tax:+.1f}% | {bw:.2f} |")
    al_vl=(statistics.mean(by["AL-VL"])/ql-1)*100
    ac_vl=(statistics.mean(by["AC-VL"])/ql-1)*100
    ratio=ac_vl/al_vl if al_vl else 0
    lines += ["", f"DR3 ratio AC-VL/AL-VL tax = {ratio:.3f}.", ""]
    REPORT.write_text("\\n".join(lines))
    print(REPORT)

if __name__ == "__main__":
    main()
