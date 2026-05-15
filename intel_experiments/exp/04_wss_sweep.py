#!/usr/bin/env python3
"""
Phase 4a — Victim Working-Set Size (WSS) Sweep

Condition A only. Sweeps victim WSS across:
  {256KB, 1MB, 4MB, 16MB, 32MB, 64MB}

Prediction (from METHODOLOGY.md §10):
  - WSS ≤ 1 MB (L2): tax near zero (lines don't reach LLC/SF)
  - WSS 4–32 MB (LLC): tax grows as SF pressure has impact
  - WSS > 60 MB (beyond LLC): victim is DRAM-bound regardless → relative tax shrinks

Aggressor core count: N_A from Phase 1 calibration (typically 2 cores for ~40 GB/s).

Outputs:
  results/processed/04_wss_sweep.csv
"""

import sys
import csv
import json
import time
import statistics
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess, VictimRun

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS   = 20
RUN_SEC    = 1.0
WARMUP_SEC = 5.0
NODE       = runner.NUMA_NODE

# Victim WSS values; all must be 2MB-hugepage-aligned
WSS_VALUES = [
    256  * 1024,         # 256 KB  (fits in L2)
    1    * 1024 * 1024,  # 1 MB    (L2 boundary)
    4    * 1024 * 1024,  # 4 MB    (LLC)
    16   * 1024 * 1024,  # 16 MB   (LLC)
    32   * 1024 * 1024,  # 32 MB   (LLC — primary experiment point)
    64   * 1024 * 1024,  # 64 MB   (exceeds 60 MB LLC)
]

# Align to 2MB hugepage boundary
HP_2M = 2 * 1024 * 1024
WSS_VALUES = [(w + HP_2M - 1) & ~(HP_2M - 1) for w in WSS_VALUES]


def load_n_a_cores() -> int:
    try:
        raw_files = sorted(runner.RESULTS_RAW.glob("01_calibration_*.json"), reverse=True)
        if raw_files:
            data = json.loads(raw_files[0].read_text())
            return data["core_counts"]["A"]["n_cores"]
    except Exception:
        pass
    return 2  # default


def run_wss_point(wss: int, n_aggr_cores: int, trials: int = N_TRIALS) -> List[Dict]:
    total_dur = WARMUP_SEC + trials * (RUN_SEC + 0.1) + 10.0

    aggressors = []
    for i in range(n_aggr_cores):
        a = AggressorProcess("A", cpu=runner.AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    runner.warmup_sleep(WARMUP_SEC)

    agg_bw = sum(a.get_recent_bw_from_stderr() or 0.0 for a in aggressors)

    victim = VictimRun(cpu=runner.VICTIM_CPU, node=NODE,
                       wss=wss, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()

    for a in aggressors:
        a.stop()

    annotated = []
    for tr in trial_results:
        annotated.append({
            "wss_bytes":       wss,
            "wss_kb":          wss // 1024,
            "trial":           tr["trial"],
            "cycles_per_load": tr["cycles_per_load"],
            "aggregate_bw_gbps": round(agg_bw, 2),
        })

    log(f"  WSS={wss//1024}KB: mean={statistics.mean(t['cycles_per_load'] for t in annotated):.1f} "
        f"cycles/load, agg_bw={agg_bw:.1f} GB/s")

    runner.save_raw({"wss_bytes": wss, "trials": annotated}, tag=f"04_wss_{wss//1024}k")
    runner.cooldown_sleep(2.0)
    return annotated


def run_quiescent_per_wss(wss: int, trials: int = 10) -> float:
    """Quick quiescent baseline for this WSS."""
    victim = VictimRun(cpu=runner.VICTIM_CPU, node=NODE,
                       wss=wss, trials=trials, run_sec=RUN_SEC)
    tr = victim.run()
    if not tr:
        return 0.0
    return statistics.mean(t["cycles_per_load"] for t in tr)


def main():
    runner.check_binaries()
    runner.check_env()

    n_a = load_n_a_cores()
    log(f"=== Phase 4a: WSS Sweep (condition A, {n_a} aggressor cores) ===")

    all_rows: List[Dict] = []
    for wss in WSS_VALUES:
        log(f"\nWSS = {wss // 1024} KB ({wss // 1024 / 1024:.1f} MB)")
        q_cycles = run_quiescent_per_wss(wss)
        log(f"  quiescent baseline: {q_cycles:.1f} cycles/load")

        rows = run_wss_point(wss, n_a, N_TRIALS)
        for r in rows:
            r["quiescent_cycles"] = round(q_cycles, 1)
            r["tax_pct"] = round((r["cycles_per_load"] - q_cycles) / q_cycles * 100, 2) \
                           if q_cycles > 0 else 0.0
        all_rows.extend(rows)

    csv_path = PROC_DIR / "04_wss_sweep.csv"
    fieldnames = ["wss_bytes","wss_kb","trial","cycles_per_load",
                  "aggregate_bw_gbps","quiescent_cycles","tax_pct"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Wrote: {csv_path}")

    print(f"\nPhase 4a complete. Results: {csv_path}")
    print("Generate WSS plot: python3 analysis/plot_sweeps.py --wss")


if __name__ == "__main__":
    main()
