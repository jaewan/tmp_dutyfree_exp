#!/usr/bin/env python3
"""Phase 19.5 scaled L2-fit control on Xeon Platinum 8592+."""

import csv
import json
import signal
import statistics
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.5_l2fit_control.csv"
REPORT = ROOT / "results" / "processed" / "19.5_l2fit_control_report.md"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase_nocap"
AGG = ROOT / "bench" / "aggressor" / "stream_wb"

N_TRIALS = 30
MEASURE_SEC = 10
NODE = 0
VICTIM_CPU = 0
AGGR_CPUS = [1, 2, 3, 4]
WSS = 384 * 1024
REGION_GB = 5
CORE_EVENTS = "mem_load_retired.l2_hit,mem_load_retired.l3_hit,mem_load_retired.l3_miss"


def parse_perf(stderr: str) -> dict[str, int]:
    out = {}
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            raw = parts[0].strip()
            ev = parts[2].strip().lower()
            try:
                out[ev] = int(float(raw))
            except ValueError:
                pass
    return out


class Agg:
    def __init__(self, cpu: int, duration: int):
        self.cpu = cpu
        self.duration = duration
        self.proc = None
        self.err = []

    def start(self):
        cmd = ["numactl", "--membind=0", "--cpunodebind=0", "--",
               str(AGG), "--cpu", str(self.cpu), "--node", "0",
               "--region-gb", str(REGION_GB), "--duration-sec", str(self.duration),
               "--no-verify"]
        self.proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.proc.stderr:
            self.err.append(line.rstrip())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill(); self.proc.wait()


def run_victim():
    cmd = ["numactl", "--membind=0", "--cpunodebind=0", "--",
           str(VICTIM), "--cpu", "0", "--node", "0", "--wss", str(WSS),
           "--trials", "1", "--run-sec", str(MEASURE_SEC)]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    perf = subprocess.Popen(["perf", "stat", "-x,", "-C", "0", "-e", CORE_EVENTS,
                             "--", "sleep", str(MEASURE_SEC)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=MEASURE_SEC + 30)
    _, perr = perf.communicate(timeout=MEASURE_SEC + 20)
    if proc.returncode != 0:
        raise RuntimeError(err)
    return json.loads(out)[0], parse_perf(perr)


def run_cell(label: str, active: bool):
    aggs = []
    if active:
        dur = N_TRIALS * (MEASURE_SEC + 2) + 60
        for cpu in AGGR_CPUS:
            a = Agg(cpu, dur); a.start(); aggs.append(a)
        time.sleep(8)
    rows = []
    try:
        for t in range(N_TRIALS):
            d, pmu = run_victim()
            l2 = max(pmu.get("mem_load_retired.l2_hit", 0), 0)
            l3h = max(pmu.get("mem_load_retired.l3_hit", 0), 0)
            l3m = max(pmu.get("mem_load_retired.l3_miss", 0), 0)
            denom = l2 + l3h + l3m
            row = {
                "condition": label,
                "trial": t,
                "platform": "Xeon Platinum 8592+",
                "victim_wss_bytes_requested": WSS,
                "victim_wss_note": "benchmark aligns to 2MB hugepage; intended L2-fit control",
                "aggressor_cores": len(aggs),
                "aggressor_region_gb_per_core": REGION_GB if active else 0,
                "cycles_per_load": round(float(d["cycles_per_load"]), 3),
                "latency_ns": round(float(d["cycles_per_load"]) / float(d["tsc_hz"]) * 1e9, 2),
                "l2_hit": l2,
                "l3_hit": l3h,
                "l3_miss": l3m,
                "l2_hit_fraction": round(l2 / denom, 6) if denom else 0,
            }
            rows.append(row)
            print(label, t, row["cycles_per_load"], row["l2_hit_fraction"], flush=True)
            time.sleep(2)
    finally:
        for a in aggs:
            a.stop()
    return rows


def main():
    rows = run_cell("Q_L2", False) + run_cell("A_L2_scaled", True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    q = [float(r["cycles_per_load"]) for r in rows if r["condition"] == "Q_L2"]
    a = [float(r["cycles_per_load"]) for r in rows if r["condition"] == "A_L2_scaled"]
    tax = (statistics.mean(a) / statistics.mean(q) - 1) * 100
    REPORT.write_text(
        "# Phase 19.5 L2-Fit Control Report\n\n"
        f"Q mean cycles/load: {statistics.mean(q):.3f}\n\n"
        f"A_scaled mean cycles/load: {statistics.mean(a):.3f}\n\n"
        f"Tax: {tax:+.2f}%\n\n"
        "Expected: ~0% tax for L2-fit victim under scaled aggressor.\n"
    )
    print(REPORT)


if __name__ == "__main__":
    main()
