#!/usr/bin/env python3
"""
Phase 3 — Mechanism Plot: SF Eviction Rate vs. Aggregate Throughput

Sweeps aggressor core count from 1 to N_MAX, measuring (aggregate_bw, sf_eviction_rate)
for each condition (A, B, C, D). n=10 per point.

Key prediction (H5):
  - A and B: linear slope > 0, R² > 0.85
  - C: slope indistinguishable from 0

PMU events collected via perf stat (requires perf_event_paranoid ≤ 0).

Outputs:
  results/processed/03_pmu_sweep.csv
  results/processed/03_mechanism_findings.md
"""

import sys
import csv
import json
import time
import statistics
import subprocess
import threading
from pathlib import Path
from typing import List, Dict, Tuple

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

MAX_CORES_PER_COND = 16    # max aggressor cores for sweep
N_POINTS_PER_COND  = 8     # evenly distributed core counts
N_TRIALS           = 10    # per point
MEASURE_SEC        = 5.0   # PMU collection duration per trial
WARMUP_SEC         = 3.0
NODE               = runner.NUMA_NODE

# PMU events to sum across all 32 CHAs
SF_EVICT_EVENTS = [
    "unc_cha_core_snp.evict_one",
    "unc_cha_core_snp.evict_gtone",
    "unc_cha_rxc_req_q1_retry.sf_victim",
    "unc_cha_tor_inserts.ia_drd_pref",
]


def get_core_count_range(max_c: int = MAX_CORES_PER_COND,
                          n_pts: int = N_POINTS_PER_COND) -> List[int]:
    """Return evenly spaced core counts from 1 to max_c."""
    if max_c <= n_pts:
        return list(range(1, max_c + 1))
    step = max(1, max_c // n_pts)
    points = list(range(1, max_c + 1, step))
    if max_c not in points:
        points.append(max_c)
    return points[:n_pts]


def measure_cha_events(duration_sec: float) -> Dict[str, int]:
    """Run perf stat for CHA events; returns summed counts across all 32 tiles."""
    return runner.run_perf_cha_stat(duration_sec)


def run_sweep_point(condition: str, n_cores: int,
                    n_trials: int = N_TRIALS) -> List[Dict]:
    """
    For a given (condition, n_cores), run n_trials measurements.
    Each trial: start aggressors, warmup, collect PMU for MEASURE_SEC.
    Returns list of {bw_gbps, sf_evict_rate, ...}.
    """
    results = []
    total_duration = WARMUP_SEC + MEASURE_SEC + 5.0

    for trial in range(n_trials):
        # Start aggressors
        aggressors = []
        for i in range(n_cores):
            a = AggressorProcess(condition, cpu=runner.AGGR_CPUS[i],
                                 region_gb=1, duration_sec=total_duration, node=NODE)
            a.start()
            aggressors.append(a)

        time.sleep(WARMUP_SEC)

        # Measure BW from aggressor stderr
        time.sleep(2.0)  # let aggressors report at least once
        bw_samples = [a.get_recent_bw_from_stderr() or 0.0 for a in aggressors]
        agg_bw = sum(bw_samples)

        # Collect PMU counters
        pmu_counts = measure_cha_events(MEASURE_SEC)

        # Stop aggressors
        for a in aggressors:
            a.stop()

        sf_evict = (pmu_counts.get("unc_cha_core_snp.evict_one", 0) +
                    pmu_counts.get("unc_cha_core_snp.evict_gtone", 0))
        sf_evict_rate = sf_evict / MEASURE_SEC

        results.append({
            "condition":          condition,
            "n_aggr_cores":       n_cores,
            "trial":              trial,
            "agg_bw_gbps":        round(agg_bw, 2),
            "sf_evict_total":     sf_evict,
            "sf_evict_rate":      round(sf_evict_rate, 0),
            "sf_victim_retry":    pmu_counts.get("unc_cha_rxc_req_q1_retry.sf_victim", 0),
            "tor_drd_pref":       pmu_counts.get("unc_cha_tor_inserts.ia_drd_pref", 0),
            "pmu_raw":            json.dumps(pmu_counts),
        })

        log(f"  [{condition}] cores={n_cores} trial={trial}: "
            f"bw={agg_bw:.1f} GB/s, sf_evict/s={sf_evict_rate:.0f}")

        runner.cooldown_sleep(1.0)

    return results


def write_csv(all_rows: List[Dict], path: Path):
    if not all_rows:
        return
    fieldnames = ["condition","n_aggr_cores","trial","agg_bw_gbps",
                  "sf_evict_total","sf_evict_rate","sf_victim_retry",
                  "tor_drd_pref","pmu_raw"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Wrote: {path}")


def write_findings(all_rows: List[Dict], path: Path):
    from datetime import datetime

    # Group by condition
    by_cond: Dict[str, List[Dict]] = {}
    for row in all_rows:
        c = row["condition"]
        by_cond.setdefault(c, []).append(row)

    with open(path, "w") as f:
        f.write("# Phase 3 Findings — Mechanism Plot\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Data Summary by Condition\n\n")
        f.write("| Cond | BW range (GB/s) | SF evict/s range | n points × trials |\n")
        f.write("|------|-----------------|------------------|-----------------|\n")
        for cond, rows in by_cond.items():
            bw_vals  = [r["agg_bw_gbps"] for r in rows]
            ev_vals  = [r["sf_evict_rate"] for r in rows]
            n_points = len(set(r["n_aggr_cores"] for r in rows))
            f.write(f"| {cond} | {min(bw_vals):.1f}–{max(bw_vals):.1f} | "
                    f"{min(ev_vals):.0f}–{max(ev_vals):.0f} | "
                    f"{n_points}×{N_TRIALS} |\n")

        f.write("\n## H5 Evaluation\n\n")
        f.write("Linear regression (slope, R²) requires scipy — run analysis/stats.py.\n")
        f.write("Expected: A and B have positive slope (R² > 0.85); C slope ≈ 0.\n\n")

        f.write("## Notes\n\n")
        f.write("- PMU access requires perf_event_paranoid ≤ 0.\n")
        f.write("- SF eviction rate is `unc_cha_core_snp.evict_one + evict_gtone` "
                "summed across all 32 CHA tiles.\n")
        f.write("- See NEGATIVE_RESULTS.md §N1 for event name mapping.\n")

    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 3: PMU Sweep (SF Eviction vs. Throughput) ===")

    # Check perf access
    test_result = subprocess.run(
        ["perf", "stat", "-e", "uncore_cha_0/unc_cha_clockticks/",
         "-a", "--", "sleep", "0.1"],
        capture_output=True, text=True, timeout=10
    )
    if test_result.returncode != 0:
        log("ERROR: uncore perf stat failed")
        log(f"  {test_result.stderr[:200]}")
        log("  Run: sudo env/setup.sh (set perf_event_paranoid=-1)")
        sys.exit(1)
    log("Uncore PMU access: OK")

    core_counts_range = get_core_count_range()
    log(f"Core count sweep: {core_counts_range}")

    all_rows: List[Dict] = []
    for condition in ["A", "B", "C"]:
        log(f"\nSweeping condition {condition}...")
        for n_cores in core_counts_range:
            rows = run_sweep_point(condition, n_cores, N_TRIALS)
            all_rows.extend(rows)
            runner.save_raw({"condition": condition, "n_cores": n_cores,
                             "rows": rows}, tag=f"03_pmu_{condition}_{n_cores}c")
            time.sleep(2.0)

    csv_path     = PROC_DIR / "03_pmu_sweep.csv"
    findings_path = PROC_DIR / "03_mechanism_findings.md"
    write_csv(all_rows, csv_path)
    write_findings(all_rows, findings_path)

    print(f"\nPhase 3 complete. Results: {csv_path}")
    print("Generate mechanism plot: python3 analysis/plot_mechanism.py")


if __name__ == "__main__":
    main()
