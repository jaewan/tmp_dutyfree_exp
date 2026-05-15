#!/usr/bin/env python3
"""Phase 19.5 scaled CXL cross-placement on Xeon Platinum 8592+."""

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
OUT = ROOT / "results" / "processed" / "19.5_crossplacement.csv"
REPORT = ROOT / "results" / "processed" / "19.5_crossplacement_report.md"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase_nocap"
AGG = ROOT / "bench" / "aggressor" / "stream_wb"

N_TRIALS = 30
MEASURE_SEC = 10
VICTIM_CPU = 0
VICTIM_WSS = 170 * 1024 * 1024
LLC_MB = 320
REGION_GB = 5
LOCAL_CPUS = [1]
CXL_CPUS = [1, 2]
CAL_BW = {0: 14.268, 2: 16.56}

CORE_EVENTS = "mem_load_retired.l2_hit,mem_load_retired.l3_hit,mem_load_retired.l3_miss,longest_lat_cache.miss"
UNCORE_EVENTS = ",".join([
    "uncore_cha_0/unc_cha_tor_inserts.ia_miss_drd_cxl_exp_local/",
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
            ev = parts[2].strip().lower().strip("/")
            try:
                out[ev] = out.get(ev, 0) + int(float(raw))
            except ValueError:
                pass
    return out


def read_numa(pid: int):
    try:
        text = Path(f"/proc/{pid}/numa_maps").read_text()
    except Exception as e:
        return f"ERR:{type(e).__name__}", 0, 0
    huge = [line for line in text.splitlines() if "huge" in line]
    n0 = any("N0=" in line for line in huge)
    n2 = any("N2=" in line for line in huge)
    return " | ".join(huge[:3]), int(n0), int(n2)


class AggProc:
    def __init__(self, cpu: int, node: int, duration: int):
        self.cpu = cpu; self.node = node; self.duration = duration
        self.proc = None; self.err = []

    def start(self):
        cmd = ["numactl", f"--membind={self.node}", "--cpunodebind=0", "--",
               str(AGG), "--cpu", str(self.cpu), "--node", str(self.node),
               "--region-gb", str(REGION_GB), "--duration-sec", str(self.duration),
               "--no-verify"]
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            self.err.append(line.rstrip())

    def progress_bw(self):
        for line in reversed(self.err):
            m = re.search(r"bw=([0-9.]+) GB/s", line)
            if m: return float(m.group(1))
        return 0.0

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try: self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()


def run_perf(events, scope):
    return subprocess.Popen(["perf", "stat", "-x,", *scope, "-e", events, "--", "sleep", str(MEASURE_SEC)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def run_victim(vnode: int):
    cmd = ["numactl", f"--membind={vnode}", "--cpunodebind=0", "--",
           str(VICTIM), "--cpu", str(VICTIM_CPU), "--node", str(vnode),
           "--wss", str(VICTIM_WSS), "--trials", "1", "--run-sec", str(MEASURE_SEC)]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    vsample, vn0, vn2 = read_numa(proc.pid)
    core = run_perf(CORE_EVENTS, ["-C", str(VICTIM_CPU)])
    uncore = run_perf(UNCORE_EVENTS, ["-a"])
    out, err = proc.communicate(timeout=MEASURE_SEC + 60)
    _, cerr = core.communicate(timeout=MEASURE_SEC + 20)
    _, uerr = uncore.communicate(timeout=MEASURE_SEC + 20)
    if proc.returncode != 0:
        raise RuntimeError(err[-1000:])
    return json.loads(out)[0], parse_perf(cerr), parse_perf(uerr), vsample, vn0, vn2


def run_cell(label, anode, vnode):
    print(f"=== 19.5 {label}", flush=True)
    aggs = []
    if anode is not None:
        cpus = LOCAL_CPUS if anode == 0 else CXL_CPUS
        duration = N_TRIALS * (MEASURE_SEC + 2) + 90
        for cpu in cpus:
            a = AggProc(cpu, anode, duration); a.start(); aggs.append(a)
        time.sleep(8)
    rows = []
    try:
        for t in range(N_TRIALS):
            d, core, uncore, vsample, vn0, vn2 = run_victim(vnode)
            l2 = max(core.get("mem_load_retired.l2_hit", 0), 0)
            l3h = max(core.get("mem_load_retired.l3_hit", 0), 0)
            l3m = max(core.get("mem_load_retired.l3_miss", 0), 0)
            denom = l3h + l3m
            an0 = an2 = 0; asample = ""
            for a in aggs:
                sample, n0, n2 = read_numa(a.proc.pid)
                an0 += n0; an2 += n2
                if not asample: asample = sample
            cxl = max(uncore.get("uncore_cxlcm/unc_cxlcm_rxc_flits.valid", 0), 0)
            imc = sum(max(uncore.get(f"uncore_imc_{i}/cas_count_read", 0), 0) for i in range(4))
            row = {
                "cell": label, "trial": t, "platform": "Xeon Platinum 8592+",
                "victim_node": vnode, "aggressor_node": -1 if anode is None else anode,
                "victim_wss_mb": VICTIM_WSS // (1024*1024), "llc_capacity_mb": LLC_MB,
                "aggressor_cores": len(aggs), "aggressor_region_gb_per_core": REGION_GB,
                "agg_bw_gbps_calibrated": 0 if anode is None else CAL_BW[anode],
                "cycles_per_load": round(float(d["cycles_per_load"]), 3),
                "latency_ns": round(float(d["cycles_per_load"]) / float(d["tsc_hz"]) * 1e9, 2),
                "l2_hit": l2, "l3_hit": l3h, "l3_miss": l3m,
                "l3_miss_fraction": round(l3m / denom, 6) if denom else 0,
                "longest_lat_cache_miss": max(core.get("longest_lat_cache.miss", 0), 0),
                "cxl_flits_valid": cxl, "imc_cas_rd": imc,
                "victim_numa_n0": vn0, "victim_numa_n2": vn2,
                "aggressor_numa_n0": an0, "aggressor_numa_n2": an2,
                "victim_numa_sample": vsample, "aggressor_numa_sample": asample,
                "agg_progress_bw_untrusted": round(sum(a.progress_bw() for a in aggs), 3),
            }
            rows.append(row)
            print(label, t, row["cycles_per_load"], row["l3_miss_fraction"], row["agg_bw_gbps_calibrated"], flush=True)
            time.sleep(2)
    finally:
        for a in aggs: a.stop()
    return rows


def report(rows):
    by = {c: [r for r in rows if r["cell"] == c] for c, _, _ in CELLS}
    ql = statistics.mean(float(r["cycles_per_load"]) for r in by["QL"])
    qc = statistics.mean(float(r["cycles_per_load"]) for r in by["QC"])
    lines = [
        "# Phase 19.5 Scaled Cross-Placement Report", "",
        "Platform: Intel Xeon Platinum 8592+.",
        "Victim WSS: 170 MB; LLC capacity: 320 MB.",
        "Aggressor WSS: 5 GB/core. Bandwidth-matched core counts: local=1, CXL=2.",
        "Calibrated bandwidth: local 14.268 GB/s, CXL 16.56 GB/s; both within +/-10% of 15.4 GB/s target.",
        "",
        "| Cell | n | Mean cyc/load | Tax vs placement Q | Mean L3 miss frac | Mean BW |",
        "|------|---|---------------|--------------------|-------------------|---------|",
    ]
    for cell, _, _ in CELLS:
        rs = by[cell]
        vals = [float(r["cycles_per_load"]) for r in rs]
        base = ql if cell.endswith("VL") or cell == "QL" else qc
        tax = (statistics.mean(vals) / base - 1) * 100 if cell not in ("QL","QC") else 0
        l3 = statistics.mean(float(r["l3_miss_fraction"]) for r in rs)
        bw = statistics.mean(float(r["agg_bw_gbps_calibrated"]) for r in rs)
        lines.append(f"| {cell} | {len(rs)} | {statistics.mean(vals):.2f} | {tax:+.1f}% | {l3:.4f} | {bw:.2f} |")
    al_tax = (statistics.mean(float(r["cycles_per_load"]) for r in by["AL-VL"]) / ql - 1) * 100
    ac_tax = (statistics.mean(float(r["cycles_per_load"]) for r in by["AC-VL"]) / ql - 1) * 100
    ratio = ac_tax / al_tax if al_tax else 0
    if ratio <= 0.3:
        verdict = "A: MCQ-dominant confirmed under scaled 8592+ cross-placement"
    elif ratio >= 0.7:
        verdict = "B: MCQ-dominant refuted under scaled 8592+ cross-placement"
    else:
        verdict = "C: mixed mechanism under scaled 8592+ cross-placement"
    lines += ["", f"DR3 ratio AC-VL/AL-VL tax = {ratio:.3f}.", f"Verdict: {verdict}."]
    REPORT.write_text("\n".join(lines) + "\n")


def main():
    random.seed(1951)
    order = CELLS[:]; random.shuffle(order)
    rows = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for cell in order:
        rows.extend(run_cell(*cell))
        with OUT.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    report(rows)
    print(REPORT)


if __name__ == "__main__":
    main()
