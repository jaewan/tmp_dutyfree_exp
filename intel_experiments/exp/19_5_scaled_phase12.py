#!/usr/bin/env python3
"""Phase 19.5 scaled Phase 12 replication on Xeon Platinum 8592+."""

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
OUT = ROOT / "results" / "processed" / "19.5_scaled_phase12.csv"
REPORT = ROOT / "results" / "processed" / "19.5_scaled_phase12_report.md"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase_nocap"
AGG_BINS = {
    "A": ROOT / "bench" / "aggressor" / "stream_wb",
    "B": ROOT / "bench" / "aggressor" / "stream_wb_nopf",
    "C": ROOT / "bench" / "aggressor" / "stream_wc",
    "D": ROOT / "bench" / "aggressor" / "stream_nt",
}

N_TRIALS = 30
MEASURE_SEC = 10
WARMUP_SEC = 8
COOLDOWN_SEC = 2
VICTIM_CPU = 0
AGGR_CPUS = [1, 2, 3, 4]
NODE = 0
VICTIM_WSS = 170 * 1024 * 1024
AGGR_REGION_GB = 5
LLC_MB = 320

CORE_EVENTS = ",".join([
    "mem_load_retired.l2_hit",
    "mem_load_retired.l3_hit",
    "mem_load_retired.l3_miss",
    "offcore_requests_outstanding.data_rd",
])
UNCORE_EVENTS = ",".join([
    "uncore_imc/cas_count_read/",
    "uncore_imc_free_running/rpq_cycles/",
])

CELLS = ["Q", "A", "B", "C", "D"]


def parse_perf(stderr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            raw = parts[0].strip()
            event = parts[2].strip().lower().strip("/")
            if raw.startswith("<"):
                out[event] = -1
                continue
            try:
                out[event] = out.get(event, 0) + int(float(raw))
            except ValueError:
                pass
    return out


def read_numa(pid: int) -> tuple[str, int, int]:
    try:
        text = Path(f"/proc/{pid}/numa_maps").read_text()
    except Exception as e:
        return f"ERR:{type(e).__name__}", 0, 0
    huge = [line for line in text.splitlines() if "huge" in line]
    n0 = any("N0=" in line for line in huge)
    n2 = any("N2=" in line for line in huge)
    return " | ".join(huge[:3]), int(n0), int(n2)


class Aggressor:
    def __init__(self, condition: str, cpu: int, duration: int):
        self.condition = condition
        self.cpu = cpu
        self.duration = duration
        self.proc = None
        self.err: list[str] = []

    def start(self):
        bin_path = AGG_BINS[self.condition]
        cmd = [
            "numactl", f"--membind={NODE}", "--cpunodebind=0", "--",
            str(bin_path),
            "--cpu", str(self.cpu),
            "--node", str(NODE),
            "--region-gb", str(AGGR_REGION_GB),
            "--duration-sec", str(self.duration),
        ]
        if self.condition in ("A", "D"):
            cmd.append("--no-verify")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            self.err.append(line.rstrip())

    def progress_bw(self) -> float:
        for line in reversed(self.err):
            m = re.search(r"bw=([0-9.]+) GB/s", line)
            if m:
                return float(m.group(1))
        return 0.0

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def run_perf(events: str, scope: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        ["perf", "stat", "-x,", *scope, "-e", events, "--", "sleep", str(MEASURE_SEC)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def run_victim():
    cmd = [
        "numactl", f"--membind={NODE}", "--cpunodebind=0", "--",
        str(VICTIM),
        "--cpu", str(VICTIM_CPU),
        "--node", str(NODE),
        "--wss", str(VICTIM_WSS),
        "--trials", "1",
        "--run-sec", str(MEASURE_SEC),
    ]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.0)
    numa_sample, vn0, vn2 = read_numa(proc.pid)
    core = run_perf(CORE_EVENTS, ["-C", str(VICTIM_CPU)])
    uncore = run_perf(UNCORE_EVENTS, ["-a"])
    out, err = proc.communicate(timeout=MEASURE_SEC + 60)
    _, core_err = core.communicate(timeout=MEASURE_SEC + 20)
    _, uncore_err = uncore.communicate(timeout=MEASURE_SEC + 20)
    if proc.returncode != 0:
        raise RuntimeError(err[-1000:])
    data = json.loads(out)[0]
    return data, parse_perf(core_err), parse_perf(uncore_err), numa_sample, vn0, vn2


def run_cell(condition: str) -> list[dict]:
    print(f"=== 19.5 {condition}", flush=True)
    aggs: list[Aggressor] = []
    if condition != "Q":
        duration = N_TRIALS * (MEASURE_SEC + COOLDOWN_SEC + 2) + 90
        for cpu in AGGR_CPUS:
            a = Aggressor(condition, cpu, duration)
            a.start()
            aggs.append(a)
        time.sleep(WARMUP_SEC)
    rows: list[dict] = []
    try:
        for trial in range(N_TRIALS):
            data, core, uncore, vsample, vn0, vn2 = run_victim()
            l2 = max(core.get("mem_load_retired.l2_hit", 0), 0)
            l3h = max(core.get("mem_load_retired.l3_hit", 0), 0)
            l3m = max(core.get("mem_load_retired.l3_miss", 0), 0)
            offcore = max(core.get("offcore_requests_outstanding.data_rd", 0), 0)
            denom = l3h + l3m
            l3_miss_frac = l3m / denom if denom else 0.0
            agg_n0 = agg_n2 = 0
            asample = ""
            for a in aggs:
                sample, n0, n2 = read_numa(a.proc.pid)
                agg_n0 += n0
                agg_n2 += n2
                if not asample:
                    asample = sample
            imc_cas = max(uncore.get("uncore_imc/cas_count_read", 0), 0)
            rpq = max(uncore.get("uncore_imc_free_running/rpq_cycles", 0), 0)
            row = {
                "condition": condition,
                "trial": trial,
                "platform": "Xeon Platinum 8592+",
                "victim_wss_mb": VICTIM_WSS // (1024 * 1024),
                "llc_capacity_mb": LLC_MB,
                "aggressor_cores": len(aggs),
                "aggressor_region_gb_per_core": AGGR_REGION_GB if aggs else 0,
                "cycles_per_load": round(float(data["cycles_per_load"]), 3),
                "latency_ns": round(float(data["cycles_per_load"]) / float(data["tsc_hz"]) * 1e9, 2),
                "tsc_hz": int(data["tsc_hz"]),
                "l2_hit": l2,
                "l3_hit": l3h,
                "l3_miss": l3m,
                "l3_miss_fraction": round(l3_miss_frac, 6),
                "offcore_outstanding_data_rd": offcore,
                "imc_cas_read": imc_cas,
                "imc_read_bw_gbps": round(imc_cas * 64 / MEASURE_SEC / 1e9, 3),
                "rpq_cycles": rpq,
                "agg_progress_bw_untrusted": round(sum(a.progress_bw() for a in aggs), 3),
                "victim_numa_n0": vn0,
                "victim_numa_n2": vn2,
                "aggressor_numa_n0": agg_n0,
                "aggressor_numa_n2": agg_n2,
                "victim_numa_sample": vsample,
                "aggressor_numa_sample": asample,
            }
            rows.append(row)
            print(condition, trial, row["cycles_per_load"], row["l3_miss_fraction"], row["imc_read_bw_gbps"], flush=True)
            time.sleep(COOLDOWN_SEC)
    finally:
        for a in aggs:
            a.stop()
    return rows


def summarize(rows: list[dict]):
    by = {c: [r for r in rows if r["condition"] == c] for c in CELLS}
    q_mean = statistics.mean(float(r["cycles_per_load"]) for r in by["Q"])
    lines = [
        "# Phase 19.5 Scaled Phase 12 Report",
        "",
        "Platform: Intel Xeon Platinum 8592+.",
        f"Victim WSS: {VICTIM_WSS // (1024 * 1024)} MB; LLC capacity: {LLC_MB} MB; victim fraction: {VICTIM_WSS / (1024*1024) / LLC_MB:.3f}.",
        f"Aggressor footprint: {len(AGGR_CPUS)} cores x {AGGR_REGION_GB} GB/core = {len(AGGR_CPUS)*AGGR_REGION_GB} GB.",
        "",
        "| Cond | n | Mean cyc/load | Median cyc/load | Tax vs Q | Mean L3 miss frac | Mean iMC read GB/s |",
        "|------|---|---------------|-----------------|----------|-------------------|--------------------|",
    ]
    for c in CELLS:
        rs = by[c]
        vals = [float(r["cycles_per_load"]) for r in rs]
        l3 = [float(r["l3_miss_fraction"]) for r in rs]
        bw = [float(r["imc_read_bw_gbps"]) for r in rs]
        mean = statistics.mean(vals)
        tax = (mean / q_mean - 1) * 100 if c != "Q" else 0.0
        lines.append(f"| {c} | {len(rs)} | {mean:.2f} | {statistics.median(vals):.2f} | {tax:+.1f}% | {statistics.mean(l3):.4f} | {statistics.mean(bw):.2f} |")
    a_mean = statistics.mean(float(r["cycles_per_load"]) for r in by["A"])
    a_tax = (a_mean / q_mean - 1) * 100
    verdict = "CONFIRMED" if 150 <= a_tax <= 250 else ("FALSIFIED" if a_tax < 50 else "PARTIAL")
    lines += [
        "",
        f"H19 verdict: **{verdict}**. A tax = {a_tax:+.1f}%; prediction was +150% to +250%, falsifier < +50%.",
    ]
    REPORT.write_text("\n".join(lines) + "\n")


def main():
    random.seed(1950)
    order = CELLS[:]
    random.shuffle(order)
    all_rows: list[dict] = []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for c in order:
        all_rows.extend(run_cell(c))
        with OUT.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(all_rows[0]))
            writer.writeheader()
            writer.writerows(all_rows)
    summarize(all_rows)
    print(REPORT)


if __name__ == "__main__":
    main()
