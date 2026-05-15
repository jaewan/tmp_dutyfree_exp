#!/usr/bin/env python3
"""
Phase 4b — Aggressor Count Sweep

Condition A, victim WSS = 32 MB.
Sweeps aggressor core count from 1 to 16.

Outputs:
  results/processed/05_aggressor_sweep.csv
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

N_TRIALS    = 20
RUN_SEC     = 1.0
WSS_BYTES   = 32 * 1024 * 1024
WARMUP_SEC  = 5.0
NODE        = runner.NUMA_NODE
MAX_AGGR    = 16
CORE_COUNTS = [1, 2, 4, 6, 8, 10, 12, 14, 16]


def run_aggr_count_point(n_aggr: int, trials: int = N_TRIALS) -> List[Dict]:
    total_dur = WARMUP_SEC + trials * (RUN_SEC + 0.1) + 10.0

    aggressors = []
    for i in range(n_aggr):
        a = AggressorProcess("A", cpu=runner.AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    runner.warmup_sleep(WARMUP_SEC)

    agg_bw = sum(a.get_recent_bw_from_stderr() or 0.0 for a in aggressors)

    victim = VictimRun(cpu=runner.VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()

    for a in aggressors:
        a.stop()

    annotated = []
    for tr in trial_results:
        annotated.append({
            "n_aggr_cores":    n_aggr,
            "trial":           tr["trial"],
            "cycles_per_load": tr["cycles_per_load"],
            "aggregate_bw_gbps": round(agg_bw, 2),
        })

    log(f"  {n_aggr} aggressors: mean={statistics.mean(t['cycles_per_load'] for t in annotated):.1f} "
        f"cycles/load, agg_bw={agg_bw:.1f} GB/s")

    runner.save_raw({"n_aggr": n_aggr, "trials": annotated}, tag=f"05_aggr_{n_aggr}")
    runner.cooldown_sleep(2.0)
    return annotated


def main():
    runner.check_binaries()
    runner.check_env()

    log(f"=== Phase 4b: Aggressor Count Sweep (condition A, WSS={WSS_BYTES//1024//1024} MB) ===")

    # Baseline quiescent
    log("Quiescent baseline...")
    victim = VictimRun(cpu=runner.VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=10, run_sec=RUN_SEC)
    q_trials = victim.run()
    q_mean = statistics.mean(t["cycles_per_load"] for t in q_trials) if q_trials else 0.0
    log(f"  quiescent: {q_mean:.1f} cycles/load")

    all_rows: List[Dict] = []
    for n_aggr in CORE_COUNTS:
        if n_aggr > len(runner.AGGR_CPUS):
            log(f"  Skipping n={n_aggr} (only {len(runner.AGGR_CPUS)} aggressor CPUs available)")
            continue
        log(f"\nn_aggressors = {n_aggr}")
        rows = run_aggr_count_point(n_aggr, N_TRIALS)
        for r in rows:
            r["quiescent_cycles"] = round(q_mean, 1)
            r["tax_pct"] = round((r["cycles_per_load"] - q_mean) / q_mean * 100, 2) \
                           if q_mean > 0 else 0.0
        all_rows.extend(rows)

    csv_path = PROC_DIR / "05_aggressor_sweep.csv"
    fieldnames = ["n_aggr_cores","trial","cycles_per_load","aggregate_bw_gbps",
                  "quiescent_cycles","tax_pct"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Wrote: {csv_path}")

    print(f"\nPhase 4b complete. Results: {csv_path}")
    print("Generate aggressor sweep plot: python3 analysis/plot_sweeps.py --aggressor")


if __name__ == "__main__":
    main()
