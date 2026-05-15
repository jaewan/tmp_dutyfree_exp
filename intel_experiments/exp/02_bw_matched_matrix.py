#!/usr/bin/env python3
"""
Phase 2-BW — Bandwidth-Matched A vs B Matrix (H7 follow-up)

H7 FAILED: A=15.9 GB/s (2c) vs B=20.4 GB/s (3c), 22% mismatch.
The original Phase 2 A vs B comparison is therefore bandwidth-confounded.

This experiment runs conditions A and B at *equal* core counts (2, 3, 4),
with simultaneous DRAM bandwidth measurement via iMC PMU. At each core count
we get a (victim_latency, DRAM_bw) pair per condition, allowing:

  1. Comparison of A vs B at matched core count (latency difference not BW-driven)
  2. Check if A > B persists across all tested core counts
  3. Exact BW numbers at each matching point

n = 20 trials per (condition, core_count) cell.
Also runs quiescent (Q) once for reference.

Outputs:
  results/processed/02_bw_matched_matrix.csv
  results/processed/02_bw_matched_report.md
"""

import sys
import csv
import json
import time
import statistics
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess, VictimRun

PROC_DIR     = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS     = 20
RUN_SEC      = 1.0
WSS_BYTES    = 32 * 1024 * 1024   # 32 MB — same as Phase 2
WARMUP_SEC   = 5.0
COOLDOWN_SEC = 3.0
VICTIM_CPU   = runner.VICTIM_CPU
AGGR_CPUS    = runner.AGGR_CPUS
NODE         = runner.NUMA_NODE

# Test A and B at these core counts to find matched BW points
TEST_CORE_COUNTS = [2, 3, 4]

# iMC channels to sum (socket 0)
IMC_CHANNELS = 4


def measure_dram_bw_snapshot(duration_sec: float = 3.0) -> Dict[str, float]:
    """Measure DRAM read+write bandwidth via iMC uncore PMU (socket 0)."""
    imc_events = []
    for i in range(IMC_CHANNELS):
        imc_events.append(f"uncore_imc_{i}/unc_m_cas_count.rd/")
        imc_events.append(f"uncore_imc_{i}/unc_m_cas_count.wr/")

    cmd = ["perf", "stat", "-a", "--no-big-num",
           "-e", ",".join(imc_events),
           "--", "sleep", f"{duration_sec:.1f}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=duration_sec + 20)
    except subprocess.TimeoutExpired:
        log("  iMC perf stat timed out")
        return {"total_gbps": 0.0, "fallback": True}

    totals = {"rd": 0, "wr": 0}
    for line in result.stderr.splitlines():
        m = re.match(r'\s*([\d,]+)\s+uncore_imc_\d+/(unc_m_cas_count\.(rd|wr))/', line)
        if m:
            count = int(m.group(1).replace(",", ""))
            totals[m.group(3)] += count

    if totals["rd"] == 0 and totals["wr"] == 0:
        log("  WARNING: iMC events returned 0")
        return {"read_gbps": 0.0, "write_gbps": 0.0, "total_gbps": 0.0, "fallback": True}

    BYTES_PER_CAS = 64
    rd_gbps = totals["rd"] * BYTES_PER_CAS / duration_sec / 1e9
    wr_gbps = totals["wr"] * BYTES_PER_CAS / duration_sec / 1e9
    return {
        "read_gbps":  round(rd_gbps, 2),
        "write_gbps": round(wr_gbps, 2),
        "total_gbps": round(rd_gbps + wr_gbps, 2),
        "fallback":   False,
    }


def run_cell(condition: str, n_cores: int, trials: int = N_TRIALS) -> List[Dict]:
    """
    Run one (condition, core_count) cell:
      - Start aggressors
      - Warmup
      - Measure DRAM BW snapshot (iMC PMU, 3s window)
      - Collect n victim trials
      - Stop aggressors
    Returns list of per-trial dicts, each tagged with measured DRAM BW.
    """
    log(f"\n--- [{condition}] {n_cores} cores ---")

    total_dur = WARMUP_SEC + trials * (RUN_SEC + 0.1) + COOLDOWN_SEC + 10.0
    aggressors = []
    for i in range(n_cores):
        a = AggressorProcess(condition, cpu=AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    # Wait for aggressors to reach steady state
    log(f"  Warmup {WARMUP_SEC}s ...")
    time.sleep(WARMUP_SEC)

    # MSR check for condition B
    if condition == "B":
        for i in range(n_cores):
            cpu = AGGR_CPUS[i]
            val = runner.read_msr(cpu, 0x1A4)
            if val is not None and (val & 0xF) != 0xF:
                log(f"  WARNING: cpu{cpu} MSR 0x1A4 = 0x{val:x} (expected 0xF for cond B)")

    # DRAM BW snapshot during steady-state (3s window, overlapping victim trials)
    log("  Measuring DRAM BW (3s PMU window) ...")
    bw = measure_dram_bw_snapshot(duration_sec=3.0)
    log(f"  DRAM BW: {bw.get('total_gbps', 0):.1f} GB/s "
        f"(rd={bw.get('read_gbps',0):.1f}, wr={bw.get('write_gbps',0):.1f})")

    # Allow a brief settle after perf stat
    time.sleep(1.0)

    # Collect victim trials
    victim = VictimRun(cpu=VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()

    # Aggressor-reported BW (from stderr progress lines)
    agg_reported_bw = sum(a.get_recent_bw_from_stderr() or 0.0 for a in aggressors)

    for a in aggressors:
        a.stop()

    mean_cycles = statistics.mean(t["cycles_per_load"] for t in trial_results)
    log(f"  [{condition}@{n_cores}c] mean={mean_cycles:.1f} cyc/load, "
        f"imc_bw={bw.get('total_gbps',0):.1f} GB/s, "
        f"agg_reported_bw={agg_reported_bw:.1f} GB/s")

    annotated = []
    for tr in trial_results:
        annotated.append({
            "condition":        condition,
            "n_cores":          n_cores,
            "trial":            tr["trial"],
            "cycles_per_load":  tr["cycles_per_load"],
            "total_loads":      tr.get("total_loads", 0),
            "elapsed_sec":      tr.get("elapsed_sec", 0),
            "imc_bw_total_gbps": bw.get("total_gbps", 0.0),
            "imc_bw_read_gbps":  bw.get("read_gbps",  0.0),
            "imc_bw_write_gbps": bw.get("write_gbps", 0.0),
            "imc_fallback":      bw.get("fallback", True),
            "agg_reported_bw":  round(agg_reported_bw, 2),
        })

    runner.save_raw(
        {"condition": condition, "n_cores": n_cores,
         "trials": annotated, "imc_bw": bw},
        tag=f"02_bwmatch_{condition}_{n_cores}c"
    )
    time.sleep(COOLDOWN_SEC)
    return annotated


def run_quiescent(trials: int = N_TRIALS) -> List[Dict]:
    log("\n--- Condition Q (quiescent) ---")
    victim = VictimRun(cpu=VICTIM_CPU, node=NODE,
                       wss=WSS_BYTES, trials=trials, run_sec=RUN_SEC)
    trial_results = victim.run()
    annotated = []
    for tr in trial_results:
        annotated.append({
            "condition":         "Q",
            "n_cores":           0,
            "trial":             tr["trial"],
            "cycles_per_load":   tr["cycles_per_load"],
            "total_loads":       tr.get("total_loads", 0),
            "elapsed_sec":       tr.get("elapsed_sec", 0),
            "imc_bw_total_gbps": 0.0,
            "imc_bw_read_gbps":  0.0,
            "imc_bw_write_gbps": 0.0,
            "imc_fallback":      False,
            "agg_reported_bw":   0.0,
        })
    q_mean = statistics.mean(t["cycles_per_load"] for t in annotated)
    log(f"  Q: mean={q_mean:.1f} cyc/load")
    runner.save_raw({"condition": "Q", "trials": annotated}, tag="02_bwmatch_Q")
    time.sleep(COOLDOWN_SEC)
    return annotated


def write_csv(all_rows: List[Dict], path: Path):
    fieldnames = [
        "condition", "n_cores", "trial", "cycles_per_load",
        "total_loads", "elapsed_sec",
        "imc_bw_total_gbps", "imc_bw_read_gbps", "imc_bw_write_gbps",
        "imc_fallback", "agg_reported_bw",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    log(f"Wrote: {path}")


def cell_stats(rows: List[Dict]) -> Dict:
    cycles = [r["cycles_per_load"] for r in rows]
    bws    = [r["imc_bw_total_gbps"] for r in rows]
    return {
        "n":        len(cycles),
        "mean":     statistics.mean(cycles),
        "std":      statistics.stdev(cycles) if len(cycles) > 1 else 0.0,
        "median":   statistics.median(cycles),
        "bw_mean":  statistics.mean(bws),
    }


def cliff_delta(x: List[float], y: List[float]) -> float:
    """Non-parametric Cliff's delta: P(x>y) - P(y>x)."""
    n, m = len(x), len(y)
    wins = sum(1 for xi in x for yj in y if xi > yj)
    losses = sum(1 for xi in x for yj in y if xi < yj)
    return (wins - losses) / (n * m)


def write_report(
    q_rows: List[Dict],
    results: Dict[Tuple[str, int], List[Dict]],
    path: Path,
):
    q_mean = statistics.mean(r["cycles_per_load"] for r in q_rows)

    with open(path, "w") as f:
        f.write("# Phase 2-BW — Bandwidth-Matched A vs B Matrix\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Motivation\n\n")
        f.write("H7 failed: Phase 2 used A=2 cores (15.9 GB/s) vs B=3 cores (20.4 GB/s),\n")
        f.write("a 22% bandwidth mismatch. This re-run holds core count equal for A and B.\n\n")

        f.write("## Quiescent Baseline\n\n")
        q_s = cell_stats(q_rows)
        f.write(f"- Q: mean={q_s['mean']:.1f}, std={q_s['std']:.1f}, "
                f"median={q_s['median']:.1f} cycles/load (n={q_s['n']})\n\n")

        f.write("## Results by (Condition, Core Count)\n\n")
        f.write("| Cond | Cores | n | Mean cyc/load | Std | Median | "
                "iMC BW (GB/s) | Tax vs Q |\n")
        f.write("|------|-------|---|--------------|-----|--------|"
                "--------------|----------|\n")

        for n_cores in TEST_CORE_COUNTS:
            for cond in ["A", "B"]:
                key = (cond, n_cores)
                if key not in results:
                    continue
                s = cell_stats(results[key])
                tax = (s["mean"] - q_mean) / q_mean * 100
                f.write(f"| {cond} | {n_cores} | {s['n']} | {s['mean']:.1f} | "
                        f"{s['std']:.1f} | {s['median']:.1f} | "
                        f"{s['bw_mean']:.1f} | +{tax:.1f}% |\n")

        f.write("\n## A vs B Comparison at Matched Core Counts\n\n")
        f.write("**H7-follow-up:** Does A > B persist when core count is held equal?\n\n")
        f.write("| Cores | A mean | B mean | A iMC BW | B iMC BW | Δ cyc | "
                "Cliff δ | BW ratio (A/B) |\n")
        f.write("|-------|--------|--------|---------|---------|-------|"
                "---------|----------------|\n")

        for n_cores in TEST_CORE_COUNTS:
            ka = ("A", n_cores)
            kb = ("B", n_cores)
            if ka not in results or kb not in results:
                continue
            sa = cell_stats(results[ka])
            sb = cell_stats(results[kb])
            a_cycles = [r["cycles_per_load"] for r in results[ka]]
            b_cycles = [r["cycles_per_load"] for r in results[kb]]
            d = cliff_delta(a_cycles, b_cycles)
            bw_ratio = sa["bw_mean"] / sb["bw_mean"] if sb["bw_mean"] > 0 else float("nan")
            delta_cyc = sa["mean"] - sb["mean"]
            sign = "+" if delta_cyc >= 0 else ""
            f.write(f"| {n_cores} | {sa['mean']:.1f} | {sb['mean']:.1f} | "
                    f"{sa['bw_mean']:.1f} | {sb['bw_mean']:.1f} | "
                    f"{sign}{delta_cyc:.1f} | {d:+.3f} | {bw_ratio:.2f}× |\n")

        f.write("\n## Interpretation\n\n")
        f.write("- **A > B at each core count** (A−B > 0, Cliff δ > 0.5): prefetcher amplification\n")
        f.write("  confirmed independent of core count / BW confound.\n")
        f.write("- **A ≈ B or A < B at matched cores**: the original Phase 2 finding\n")
        f.write("  was driven by core count difference (bandwidth confound), not prefetcher.\n")
        f.write("- **BW ratio ≈ 1.0 at matched cores**: confirms the matching worked.\n\n")

        # Print actual interpretations based on data
        f.write("### Observed:\n\n")
        confirmed_cores = []
        refuted_cores = []
        for n_cores in TEST_CORE_COUNTS:
            ka = ("A", n_cores)
            kb = ("B", n_cores)
            if ka not in results or kb not in results:
                continue
            a_cycles = [r["cycles_per_load"] for r in results[ka]]
            b_cycles = [r["cycles_per_load"] for r in results[kb]]
            d = cliff_delta(a_cycles, b_cycles)
            sa = cell_stats(results[ka])
            sb = cell_stats(results[kb])
            if d > 0.5 and sa["mean"] > sb["mean"]:
                confirmed_cores.append(n_cores)
            else:
                refuted_cores.append(n_cores)

        if confirmed_cores:
            f.write(f"A > B with Cliff δ > 0.5 at core counts: {confirmed_cores}\n")
            f.write("→ Prefetcher amplification CONFIRMED at bandwidth-matched comparison.\n\n")
        if refuted_cores:
            f.write(f"A NOT > B (or δ ≤ 0.5) at core counts: {refuted_cores}\n")
            f.write("→ Original Phase 2 A > B finding may be driven by bandwidth/core count confound.\n\n")

        if not confirmed_cores and not refuted_cores:
            f.write("(No cells to evaluate — check data)\n\n")

    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 2-BW: Bandwidth-Matched A vs B Matrix ===")
    log(f"Conditions: A, B at core counts {TEST_CORE_COUNTS}")
    log(f"n_trials={N_TRIALS}, WSS={WSS_BYTES//1024//1024} MB")

    all_rows: List[Dict] = []
    results: Dict[Tuple[str, int], List[Dict]] = {}

    # Quiescent baseline first
    q_rows = run_quiescent(N_TRIALS)
    all_rows.extend(q_rows)

    # Run A and B at each core count in interleaved order to reduce time bias
    # Order: A@2, B@2, A@3, B@3, A@4, B@4
    for n_cores in TEST_CORE_COUNTS:
        for cond in ["A", "B"]:
            key = (cond, n_cores)
            rows = run_cell(cond, n_cores, N_TRIALS)
            results[key] = rows
            all_rows.extend(rows)

    # Write outputs
    csv_path    = PROC_DIR / "02_bw_matched_matrix.csv"
    report_path = PROC_DIR / "02_bw_matched_report.md"
    write_csv(all_rows, csv_path)
    write_report(q_rows, results, report_path)

    print(f"\nPhase 2-BW complete.")
    print(f"  CSV:    {csv_path}")
    print(f"  Report: {report_path}")

    # Quick summary to stdout
    q_mean = statistics.mean(r["cycles_per_load"] for r in q_rows)
    print(f"\nQ baseline: {q_mean:.1f} cycles/load")
    for n_cores in TEST_CORE_COUNTS:
        for cond in ["A", "B"]:
            key = (cond, n_cores)
            if key in results:
                s = cell_stats(results[key])
                print(f"  [{cond}@{n_cores}c] mean={s['mean']:.1f} "
                      f"imc_bw={s['bw_mean']:.1f} GB/s "
                      f"tax=+{(s['mean']-q_mean)/q_mean*100:.1f}%")


if __name__ == "__main__":
    main()
