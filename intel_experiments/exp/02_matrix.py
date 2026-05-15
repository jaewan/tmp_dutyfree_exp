#!/usr/bin/env python3
"""
Phase 2 — Main Four-Condition Matrix

Goal: Quantify victim pointer-chase latency under each condition.
n=30 per condition, 1-second victim runs.

Reads Phase 1 calibration results to determine aggressor core counts.
If 01_calibration.csv not found, uses defaults from METHODOLOGY.md.

Outputs:
  results/processed/02_matrix.csv
  results/processed/02_phase_report.md
"""

import sys
import csv
import json
import time
import statistics
import subprocess
import os
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess, VictimRun

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS    = 30
RUN_SEC     = 1.0
WSS_BYTES   = 32 * 1024 * 1024   # 32 MB
WARMUP_SEC  = 5.0
COOLDOWN_SEC = 2.0
VICTIM_CPU  = runner.VICTIM_CPU
AGGR_CPUS   = runner.AGGR_CPUS
NODE        = runner.NUMA_NODE

# Default core counts if calibration not available (from METHODOLOGY.md)
DEFAULT_CORE_COUNTS = {"A": 2, "B": 8, "C": 8, "D": 2}


def load_calibration_core_counts() -> Dict[str, int]:
    """Load computed core counts from Phase 1 calibration."""
    cal_file = PROC_DIR / "01_calibration.csv"
    raw_dir  = runner.RESULTS_RAW
    # Try to find the most recent calibration raw file
    try:
        raw_files = sorted(raw_dir.glob("01_calibration_*.json"), reverse=True)
        if raw_files:
            data = json.loads(raw_files[0].read_text())
            cc = data.get("core_counts", {})
            counts = {c: cc[c]["n_cores"] for c in ["A", "B", "C"] if c in cc}
            counts["D"] = counts.get("A", DEFAULT_CORE_COUNTS["D"])
            log(f"Loaded calibration core counts: {counts}")
            return counts
    except Exception as e:
        log(f"Could not load calibration: {e}")
    log(f"Using default core counts: {DEFAULT_CORE_COUNTS}")
    return dict(DEFAULT_CORE_COUNTS)


def read_current_msr_all_aggr_cores(n_cores: int) -> Dict[int, Optional[int]]:
    vals = {}
    for i in range(n_cores):
        cpu = AGGR_CPUS[i]
        vals[cpu] = runner.read_msr(cpu, 0x1A4)
    return vals


def run_condition(condition: str, n_aggr_cores: int,
                  trials: int = N_TRIALS) -> List[Dict]:
    """
    Run one full condition: start aggressors, warmup, collect n trials,
    stop aggressors. Returns list of per-trial dicts.
    """
    log(f"\n--- Condition {condition}: {n_aggr_cores} aggressor cores ---")

    # MSR state check
    msr_before = {}
    for i in range(n_aggr_cores):
        cpu = AGGR_CPUS[i]
        msr_before[cpu] = runner.read_msr(cpu, 0x1A4)

    # Start aggressors
    total_duration = WARMUP_SEC + trials * (RUN_SEC + 0.1) + COOLDOWN_SEC + 10
    aggressors = []
    for i in range(n_aggr_cores):
        a = AggressorProcess(condition, cpu=AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_duration, node=NODE)
        a.start()
        aggressors.append(a)

    runner.warmup_sleep(WARMUP_SEC)

    # Verify MSR state for conditions requiring it
    msr_after = {}
    for i in range(n_aggr_cores):
        cpu = AGGR_CPUS[i]
        msr_after[cpu] = runner.read_msr(cpu, 0x1A4)
        if condition == "B":
            val = msr_after[cpu]
            if val is not None and (val & 0xF) != 0xF:
                log(f"  ERROR: cpu{cpu} MSR 0x1A4 = 0x{val:x} (expected 0xF for condition B)")

    # Collect victim trials
    victim = VictimRun(cpu=VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()

    # Read aggregate BW from aggressor stderr
    agg_bw = sum(a.get_recent_bw_from_stderr() or 0.0 for a in aggressors)

    # Stop aggressors
    for a in aggressors:
        a.stop()

    # Annotate each trial with condition metadata
    annotated = []
    for tr in trial_results:
        annotated.append({
            "condition":          condition,
            "n_aggr_cores":       n_aggr_cores,
            "trial":              tr["trial"],
            "cycles_per_load":    tr["cycles_per_load"],
            "total_loads":        tr.get("total_loads", 0),
            "elapsed_sec":        tr.get("elapsed_sec", 0),
            "tsc_hz":             tr.get("tsc_hz", 0),
            "aggregate_bw_gbps":  round(agg_bw, 2),
            "msr_1a4_sample":     str(msr_after.get(AGGR_CPUS[0], "N/A")),
        })

    log(f"  condition {condition}: {len(annotated)} trials, "
        f"agg_bw={agg_bw:.1f} GB/s, "
        f"mean_cycles={statistics.mean(t['cycles_per_load'] for t in annotated):.1f}")

    runner.save_raw({"condition": condition, "trials": annotated,
                     "n_aggr_cores": n_aggr_cores, "agg_bw_gbps": agg_bw},
                    tag=f"02_matrix_{condition}")
    runner.cooldown_sleep(COOLDOWN_SEC)
    return annotated


def run_quiescent(trials: int = N_TRIALS) -> List[Dict]:
    log("\n--- Condition Q (quiescent) ---")
    victim = VictimRun(cpu=VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()
    annotated = []
    for tr in trial_results:
        annotated.append({
            "condition":       "Q",
            "n_aggr_cores":    0,
            "trial":           tr["trial"],
            "cycles_per_load": tr["cycles_per_load"],
            "total_loads":     tr.get("total_loads", 0),
            "elapsed_sec":     tr.get("elapsed_sec", 0),
            "tsc_hz":          tr.get("tsc_hz", 0),
            "aggregate_bw_gbps": 0.0,
            "msr_1a4_sample":  "N/A",
        })
    log(f"  quiescent: {len(annotated)} trials, "
        f"mean={statistics.mean(t['cycles_per_load'] for t in annotated):.1f} cycles/load")
    runner.save_raw({"condition": "Q", "trials": annotated}, tag="02_matrix_Q")
    runner.cooldown_sleep(COOLDOWN_SEC)
    return annotated


def evaluate_hypotheses(all_results: Dict[str, List[Dict]]) -> str:
    """Evaluate H1–H3, H6 against the data. Returns a Markdown summary."""
    def mean_cycles(cond):
        return statistics.mean(t["cycles_per_load"] for t in all_results[cond])

    means = {c: mean_cycles(c) for c in all_results}
    q_mean = means.get("Q", 0)
    a_mean = means.get("A", 0)
    b_mean = means.get("B", 0)
    c_mean = means.get("C", 0)

    lines = ["## Hypothesis Evaluation (pre-registered)\n"]

    # H1: A > Q × 1.15
    a_over_q = (a_mean - q_mean) / q_mean * 100 if q_mean else 0
    h1_pass = a_mean >= q_mean * 1.15
    lines.append(f"**H1** (A ≥ Q×1.15): A={a_mean:.1f}, Q={q_mean:.1f}, "
                 f"ratio={a_mean/q_mean:.3f}×, Δ={a_over_q:.1f}% "
                 f"→ {'PASS' if h1_pass else 'FAIL'}\n")

    # H2: (B-Q) < 0.5×(A-Q)
    a_delta = a_mean - q_mean
    b_delta = b_mean - q_mean
    h2_pass = b_delta < 0.5 * a_delta if a_delta > 0 else False
    lines.append(f"**H2** (B-Q < 0.5×(A-Q)): Δ_A={a_delta:.1f}, Δ_B={b_delta:.1f}, "
                 f"ratio={b_delta/max(a_delta,0.001):.2f}× "
                 f"→ {'PASS' if h2_pass else 'FAIL'}\n")

    # H3: C < Q×1.02
    c_over_q = (c_mean - q_mean) / q_mean * 100 if q_mean else 0
    h3_pass = c_mean <= q_mean * 1.02
    lines.append(f"**H3** (C ≤ Q×1.02): C={c_mean:.1f}, Q={q_mean:.1f}, "
                 f"Δ={c_over_q:.2f}% "
                 f"→ {'PASS' if h3_pass else 'FAIL'}\n")

    # Paper-killing check
    if not h3_pass and c_mean > q_mean * 1.05:
        lines.append("**⚠ PAPER-KILLING: C > Q×1.05 — HALT and write BLOCKING.md**\n")

    return "\n".join(lines)


def write_csv_output(all_results: Dict[str, List[Dict]], path: Path):
    rows = []
    for cond, trials in all_results.items():
        rows.extend(trials)
    if not rows:
        return
    fieldnames = ["condition","n_aggr_cores","trial","cycles_per_load",
                  "total_loads","elapsed_sec","tsc_hz","aggregate_bw_gbps","msr_1a4_sample"]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log(f"Wrote: {path}")


def write_phase_report(all_results: Dict[str, List[Dict]],
                       core_counts: Dict[str, int], path: Path):
    from datetime import datetime

    def stats(cond):
        cycles = [t["cycles_per_load"] for t in all_results.get(cond, [])]
        if not cycles:
            return None
        return {
            "n": len(cycles),
            "mean": statistics.mean(cycles),
            "std":  statistics.stdev(cycles) if len(cycles) > 1 else 0.0,
            "median": statistics.median(cycles),
            "min":  min(cycles),
            "max":  max(cycles),
        }

    with open(path, "w") as f:
        f.write("# Phase 2 Report — Four-Condition Matrix\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Results Summary\n\n")
        f.write("| Cond | n_agg | n | Mean cycles/load | Std | Median | "
                "Agg BW (GB/s) | MSR 0x1A4 |\n")
        f.write("|------|-------|---|------------------|-----|--------|"
                "--------------|----------|\n")
        for cond in ["Q", "A", "B", "C", "D"]:
            s = stats(cond)
            if s is None:
                continue
            trials = all_results.get(cond, [])
            agg_bw = statistics.mean(t["aggregate_bw_gbps"] for t in trials) if trials else 0
            msr = trials[0]["msr_1a4_sample"] if trials else "N/A"
            f.write(f"| {cond} | {core_counts.get(cond,0)} | {s['n']} | {s['mean']:.1f} | "
                    f"{s['std']:.1f} | {s['median']:.1f} | {agg_bw:.1f} | {msr} |\n")

        f.write("\n")
        f.write(evaluate_hypotheses(all_results))

        f.write("\n## Raw Data\n\n")
        f.write("See results/processed/02_matrix.csv for per-trial data.\n")
        f.write("Statistical tests are in results/processed/05_stats_table.md "
                "(generated by Phase 5).\n")
    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 2: Four-Condition Matrix ===")
    log(f"n_trials={N_TRIALS}, run_sec={RUN_SEC}, WSS={WSS_BYTES//1024//1024} MB")

    core_counts = load_calibration_core_counts()
    log(f"Aggressor core counts: {core_counts}")

    all_results: Dict[str, List[Dict]] = {}

    # Quiescent first (baseline)
    all_results["Q"] = run_quiescent(N_TRIALS)

    # Run conditions in order A, B, C, D
    for condition in ["A", "B", "C", "D"]:
        n = core_counts.get(condition, DEFAULT_CORE_COUNTS[condition])
        if n > len(AGGR_CPUS):
            log(f"WARNING: requested {n} aggressor cores but only {len(AGGR_CPUS)} available")
            n = len(AGGR_CPUS)
        all_results[condition] = run_condition(condition, n, N_TRIALS)
        time.sleep(3.0)

    # Write outputs
    csv_path    = PROC_DIR / "02_matrix.csv"
    report_path = PROC_DIR / "02_phase_report.md"
    write_csv_output(all_results, csv_path)
    write_phase_report(all_results, core_counts, report_path)

    # Check for paper-killing condition
    if "Q" in all_results and "C" in all_results and "A" in all_results:
        q_mean = statistics.mean(t["cycles_per_load"] for t in all_results["Q"])
        c_mean = statistics.mean(t["cycles_per_load"] for t in all_results["C"])
        a_mean = statistics.mean(t["cycles_per_load"] for t in all_results["A"])
        if c_mean > q_mean * 1.05:
            print("\n⚠ PAPER-KILLING RESULT: C > Q×1.05")
            print("  This means WC/NT traffic creates SF pressure, invalidating the mechanism.")
            print("  HALT. Write BLOCKING.md and NEGATIVE_RESULTS.md before proceeding.")
            sys.exit(2)
        if a_mean < q_mean * 1.05:
            print("\n⚠ H1 FAILED: Condition A shows no meaningful victim degradation.")
            print(f"  A={a_mean:.1f} vs Q={q_mean:.1f} (< 5% increase)")
            print("  Consider running at higher aggressor counts. Document failure.")

    print(f"\nPhase 2 complete. Results: {csv_path}")
    print(f"Phase report: {report_path}")
    print("Run analysis: python3 analysis/stats.py")


if __name__ == "__main__":
    main()
