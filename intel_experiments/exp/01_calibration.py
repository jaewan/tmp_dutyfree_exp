#!/usr/bin/env python3
"""
Phase 1 — Bandwidth Calibration

Goal: For a single core, measure sustained streaming throughput under
WB+prefetch, WB-no-prefetch, and WC, on a 2MB-hugepage 1 GB region
with sequential 64-byte reads. n=10 each, document mean ± std.

Calibration sanity floors (from METHODOLOGY.md §7):
  WB+pf:    15–35 GB/s per core
  WB-nopf:   3–10 GB/s per core
  WC:        2–10 GB/s per core
  WB+pf vs WB-nopf ratio: ≥ 2×

Outputs:
  results/processed/01_calibration.csv
  results/processed/01_phase_report.md
"""

import sys
import os
import json
import time
import subprocess
import statistics
import csv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log

SCRIPT_DIR = Path(__file__).parent
PROC_DIR   = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS    = 10
DURATION_S  = 5.0    # seconds per single-core BW trial
REGION_GB   = 1
CPU         = runner.AGGR_CPUS[0]   # use CPU 1 (not victim CPU 0)
NODE        = runner.NUMA_NODE

# Sanity floors/ceilings (from METHODOLOGY.md, not to be changed post-hoc)
SANITY = {
    "A": {"floor": 15.0, "ceil": 35.0, "name": "WB+prefetch"},
    "B": {"floor":  3.0, "ceil": 10.0, "name": "WB-nopf"},
    "C": {"floor":  2.0, "ceil": 10.0, "name": "WC (MOVNTDQA)"},
}
MIN_RATIO_A_B = 2.0   # A/B must be ≥ 2×


def run_single_bw_trial(condition: str) -> float:
    """Run one BW trial; return GB/s."""
    agr = runner.AggressorProcess(condition, cpu=CPU, region_gb=REGION_GB,
                                  duration_sec=DURATION_S, node=NODE)
    agr.start()
    agr.proc.wait(timeout=DURATION_S + 30)
    bw = agr.read_final_bw()
    if bw is None:
        # Fallback: parse from stderr progress lines
        bw = agr.get_recent_bw_from_stderr() or 0.0
    return bw


def calibrate_condition(condition: str) -> dict:
    name = SANITY[condition]["name"]
    log(f"Calibrating condition {condition} ({name}), n={N_TRIALS}...")
    bw_samples = []
    for trial in range(N_TRIALS):
        bw = run_single_bw_trial(condition)
        bw_samples.append(bw)
        log(f"  trial {trial+1}/{N_TRIALS}: {bw:.2f} GB/s")
        time.sleep(1.0)

    mean = statistics.mean(bw_samples)
    std  = statistics.stdev(bw_samples) if len(bw_samples) > 1 else 0.0
    return {
        "condition": condition,
        "name": name,
        "n": N_TRIALS,
        "mean_gbps": round(mean, 3),
        "std_gbps":  round(std, 3),
        "min_gbps":  round(min(bw_samples), 3),
        "max_gbps":  round(max(bw_samples), 3),
        "samples":   [round(b, 3) for b in bw_samples],
    }


def check_sanity(results: dict) -> bool:
    ok = True
    print("\n=== Calibration Sanity Check ===")
    for cond, r in results.items():
        floor = SANITY[cond]["floor"]
        ceil  = SANITY[cond]["ceil"]
        mean  = r["mean_gbps"]
        if mean < floor:
            print(f"  FAIL [{cond}] {mean:.2f} GB/s < floor {floor} GB/s "
                  f"— {SANITY[cond]['name']}")
            ok = False
        elif mean > ceil:
            print(f"  WARN [{cond}] {mean:.2f} GB/s > ceiling {ceil} GB/s "
                  f"(unexpected — check {SANITY[cond]['name']})")
        else:
            print(f"  PASS [{cond}] {mean:.2f} ± {r['std_gbps']:.2f} GB/s "
                  f"(floor={floor}, ceil={ceil})")

    ratio = results["A"]["mean_gbps"] / max(results["B"]["mean_gbps"], 0.001)
    if ratio < MIN_RATIO_A_B:
        print(f"  FAIL A/B ratio = {ratio:.2f}× (need ≥ {MIN_RATIO_A_B}× "
              f"— prefetcher not engaging or MSR write failed)")
        ok = False
    else:
        print(f"  PASS A/B ratio = {ratio:.2f}× (≥ {MIN_RATIO_A_B}×)")

    return ok


def compute_core_counts(results: dict, target_gbps: float = 40.0) -> dict:
    """Compute aggressor core counts to match target aggregate BW ±5%."""
    counts = {}
    for cond, r in results.items():
        n = max(1, round(target_gbps / r["mean_gbps"]))
        actual = n * r["mean_gbps"]
        counts[cond] = {
            "n_cores": n,
            "predicted_agg_gbps": round(actual, 1),
        }
    return counts


def write_csv(results: dict, path: Path):
    rows = []
    for cond, r in results.items():
        for i, bw in enumerate(r["samples"]):
            rows.append({
                "condition": cond,
                "condition_name": r["name"],
                "trial": i,
                "bw_gbps": bw,
            })
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["condition","condition_name","trial","bw_gbps"])
        writer.writeheader()
        writer.writerows(rows)
    log(f"Wrote: {path}")


def write_phase_report(results: dict, core_counts: dict, passed: bool, path: Path):
    with open(path, "w") as f:
        f.write("# Phase 1 Report — Bandwidth Calibration\n")
        f.write(f"## Date: {__import__('datetime').datetime.now().isoformat()}\n")
        f.write(f"## Status: {'PASS' if passed else 'FAIL — see NEGATIVE_RESULTS.md'}\n\n")

        f.write("## Results\n\n")
        f.write("| Cond | Name | n | Mean (GB/s) | Std | Min | Max | Floor | Ceil | Pass? |\n")
        f.write("|------|------|---|-------------|-----|-----|-----|-------|------|-------|\n")
        for cond, r in results.items():
            floor = SANITY[cond]["floor"]
            ceil  = SANITY[cond]["ceil"]
            ok = floor <= r["mean_gbps"] <= ceil
            f.write(f"| {cond} | {r['name']} | {r['n']} | {r['mean_gbps']:.2f} | "
                    f"{r['std_gbps']:.2f} | {r['min_gbps']:.2f} | {r['max_gbps']:.2f} | "
                    f"{floor} | {ceil} | {'✓' if ok else '✗'} |\n")

        ratio = results["A"]["mean_gbps"] / max(results["B"]["mean_gbps"], 0.001)
        f.write(f"\nA/B bandwidth ratio: {ratio:.2f}× (required ≥ {MIN_RATIO_A_B}×)\n")

        f.write("\n## Phase 2 Core Counts (target 40 GB/s aggregate)\n\n")
        f.write("| Cond | n_cores | Predicted agg BW (GB/s) |\n")
        f.write("|------|---------|-------------------------|\n")
        for cond, cc in core_counts.items():
            f.write(f"| {cond} | {cc['n_cores']} | {cc['predicted_agg_gbps']:.1f} |\n")

        f.write("\n## Gate Conditions for Phase 2\n")
        if passed:
            f.write("All sanity checks passed. Phase 2 is authorized to run.\n")
        else:
            f.write("FAIL: One or more sanity checks failed.\n")
            f.write("See NEGATIVE_RESULTS.md for diagnosis.\n")
    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 1: Bandwidth Calibration ===")
    log(f"CPU: {CPU}, NUMA node: {NODE}, region: {REGION_GB} GB")
    log(f"n={N_TRIALS} trials × {DURATION_S:.0f} s each")

    # Check MSR access for condition B
    msr_val = runner.read_msr(CPU, 0x1A4)
    if msr_val is None:
        log("WARNING: cannot read MSR 0x1A4 — condition B (WB-nopf) will fail")
        log("  Run: sudo env/setup.sh")

    results = {}
    for cond in ["A", "B", "C"]:
        results[cond] = calibrate_condition(cond)
        time.sleep(2.0)

    passed = check_sanity(results)
    core_counts = compute_core_counts(results)

    print("\n=== Phase 2 Core Counts ===")
    for cond, cc in core_counts.items():
        print(f"  {cond}: {cc['n_cores']} cores → predicted {cc['predicted_agg_gbps']:.1f} GB/s")

    csv_path    = PROC_DIR / "01_calibration.csv"
    report_path = PROC_DIR / "01_phase_report.md"
    write_csv(results, csv_path)
    write_phase_report(results, core_counts, passed, report_path)

    # Save full results to raw/
    runner.save_raw({"phase": 1, "results": results, "core_counts": core_counts,
                     "passed": passed}, tag="01_calibration")

    if not passed:
        print("\nPHASE 1 GATE FAILED — do not proceed to Phase 2")
        print("Add an entry to NEGATIVE_RESULTS.md describing the failure")
        sys.exit(1)

    print("\nPHASE 1 GATE PASSED — proceed to Phase 2 with above core counts")
    print(f"Results: {csv_path}")


if __name__ == "__main__":
    main()
