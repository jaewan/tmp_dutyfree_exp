#!/usr/bin/env python3
"""
Phase 4-NEW — L2-Fit Victim Control (H8, H9)

Tests whether the directory tax persists when the victim's working set
fits in the private L2 cache (384 KB, well within the 2 MB per-core L2),
which eliminates LLC capacity displacement as a confound.

Design:
  - Victim WSS: 384 KB (6144 cache lines × 64 bytes)
  - Victim CPU: 0 (same as all prior phases)
  - Conditions: Q (0 aggressors), A (2 cores), B (3 cores)
  - n = 30 trials per condition
  - run_sec = 1.0 per trial (same as Phase 2)

L2 residency verification:
  - perf stat MEM_LOAD_RETIRED.L1_HIT + L2_HIT on victim CPU
  - If L2_HIT rate < 80% under Q, WSS is too large or TLB is thrashing

H8 evaluation (pre-registered):
  - tax_32MB = (A_32MB − Q_32MB) / Q_32MB  (from Phase 2 data)
  - tax_384KB = (A_384KB − Q_384KB) / Q_384KB  (from this phase)
  - H8 PASSES if tax_384KB < 0.5 × tax_32MB

H9 evaluation (pre-registered):
  - A_384KB − B_384KB > 0 at p < 0.01 (Welch), Cliff's δ > 0.5

Outputs:
  results/processed/04_new_l2fit_matrix.csv
  results/processed/04_new_l2fit_report.md
"""

import sys
import csv
import json
import time
import statistics
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess, VictimRun

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

NODE      = runner.NUMA_NODE
N_TRIALS  = 30
RUN_SEC   = 1.0

# 384 KB = 393216 bytes; must be multiple of 2MB hugepage boundary
# pointer_chase aligns to 2MB, so 393216 rounds up to 2MB = 2097152 bytes.
# However, 384 KB << 2 MB so the first 384 KB of the 2 MB hugepage will be used.
# pointer_chase builds the list over the entire WSS, so we pass 393216.
# The binary aligns up to 2MB anyway, but the list has 384K/64 = 6144 nodes.
WSS_L2FIT  = 384 * 1024       # 384 KB
WSS_32MB   = 32 * 1024 * 1024  # 32 MB (Phase 2 baseline)

# Phase 2 core counts (same as Phase 2)
PHASE2_CORES = {"A": 2, "B": 3}

# Phase 2 results for H8 baseline (from 02_phase_report.md)
PHASE2_Q = 81.4   # cycles/load quiescent
PHASE2_A = 241.8  # cycles/load condition A


def measure_l2_residency(victim_cpu: int = 0) -> Dict:
    """Run perf stat on victim CPU to check cache hit distribution."""
    events = [
        f"cpu{victim_cpu}/mem_load_retired.l1_hit/",
        f"cpu{victim_cpu}/mem_load_retired.l2_hit/",
        f"cpu{victim_cpu}/mem_load_retired.l3_hit/",
        f"cpu{victim_cpu}/mem_load_retired.l3_miss/",
    ]
    cmd = ["perf", "stat", "-e", ",".join(events), "--",
           "numactl", f"--physcpubind={victim_cpu}", f"--membind={NODE}",
           str(runner.VICTIM_BIN),
           "--cpu", str(victim_cpu),
           "--node", str(NODE),
           "--wss", str(WSS_L2FIT),
           "--trials", "5",
           "--run-sec", "1.0"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        log("  L2 residency check timed out")
        return {}

    import re
    counts = {}
    for line in result.stderr.splitlines():
        m = re.match(r'\s*([\d,]+)\s+cpu\d+/(mem_load_retired\.\w+)/', line)
        if m:
            counts[m.group(2)] = int(m.group(1).replace(",", ""))

    total = sum(counts.values())
    if total == 0:
        log("  WARNING: no cache event counts from perf stat")
        return counts

    log(f"  L2 residency check (quiescent, 384 KB WSS):")
    for ev, cnt in counts.items():
        pct = cnt / total * 100
        log(f"    {ev}: {cnt:,} ({pct:.1f}%)")

    return counts


def run_condition(condition: str, n_aggr: int, n_trials: int = N_TRIALS) -> List[Dict]:
    """Run victim with WSS=384KB against given aggressor condition."""
    log(f"  Running condition {condition} (n_aggr={n_aggr}, wss=384KB) ...")

    total_dur = n_trials * RUN_SEC + 30.0
    aggressors = []
    for i in range(n_aggr):
        a = AggressorProcess(condition, cpu=runner.AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    if n_aggr > 0:
        time.sleep(5.0)  # wait for aggressors to saturate memory

    victim = VictimRun(
        cpu=runner.VICTIM_CPU,
        node=NODE,
        wss=WSS_L2FIT,
        trials=n_trials,
        run_sec=RUN_SEC,
    )
    results = victim.run()

    for a in aggressors:
        a.stop()

    rows = []
    for r in results:
        rows.append({
            "condition":        condition,
            "n_aggr_cores":     n_aggr,
            "wss_bytes":        WSS_L2FIT,
            "trial":            r["trial"],
            "cycles_per_load":  r["cycles_per_load"],
            "total_loads":      r["total_loads"],
            "elapsed_sec":      r["elapsed_sec"],
        })

    return rows


def cliff_delta(x: List[float], y: List[float]) -> float:
    """Non-parametric Cliff's delta: P(x > y) - P(y > x)."""
    n, m = len(x), len(y)
    greater = sum(1 for xi in x for yj in y if xi > yj)
    less    = sum(1 for xi in x for yj in y if xi < yj)
    return (greater - less) / (n * m)


def welch_t(x: List[float], y: List[float]):
    """Welch t-test. Returns (t, p-value, df)."""
    try:
        from scipy import stats
        t, p = stats.ttest_ind(x, y, equal_var=False)
        return t, p
    except ImportError:
        n1, n2 = len(x), len(y)
        m1, m2 = statistics.mean(x), statistics.mean(y)
        v1 = statistics.variance(x) / n1
        v2 = statistics.variance(y) / n2
        se = (v1 + v2) ** 0.5
        t = (m1 - m2) / se if se > 0 else float("inf")
        return t, None


def eval_h8_h9(q_vals: List[float], a_vals: List[float], b_vals: List[float]) -> str:
    lines = []

    q_mean = statistics.mean(q_vals)
    a_mean = statistics.mean(a_vals)
    b_mean = statistics.mean(b_vals)

    tax_384 = (a_mean - q_mean) / q_mean
    tax_32  = (PHASE2_A - PHASE2_Q) / PHASE2_Q

    lines.append("## H8 Evaluation (L2-fit reduces Q→A tax by ≥50%)\n\n")
    lines.append(f"- Phase 2 (32 MB WSS): Q={PHASE2_Q:.1f}, A={PHASE2_A:.1f}, "
                 f"tax={tax_32*100:.1f}%\n")
    lines.append(f"- Phase 4-NEW (384 KB WSS): Q={q_mean:.1f}, A={a_mean:.1f}, "
                 f"tax={tax_384*100:.1f}%\n")
    reduction = 1.0 - (tax_384 / tax_32) if tax_32 > 0 else float("nan")
    lines.append(f"- Tax reduction: {reduction*100:.1f}% "
                 f"(H8 threshold: ≥50% → indicates LLC capacity dominates)\n")
    if reduction >= 0.50:
        lines.append("- **H8: PASSES** — LLC capacity displacement explains ≥50% of Phase 2 tax.\n")
        lines.append("  IMPLICATION: Phase 2 headline numbers are partially confounded by LLC capacity.\n")
        lines.append("  The SF-mediation claim requires qualification.\n")
    elif tax_384 > 0.05 * q_mean:
        lines.append("- **H8: FAILS** — Tax persists under L2-fit (< 50% reduction).\n")
        lines.append("  IMPLICATION: SF back-invalidations cause the tax, not LLC capacity.\n")
        lines.append("  The mechanism claim is on solid ground.\n")
    else:
        lines.append("- **H8: FAILS** — No measurable tax under L2-fit.\n")
        lines.append("  IMPLICATION: SF pressure insufficient at Phase 2 aggressor counts.\n")
        lines.append("  Need higher aggressor counts or different victim WSS.\n")

    lines.append("\n## H9 Evaluation (A > B under L2-fit, large effect)\n\n")
    a_b_diff = a_mean - b_mean
    cd = cliff_delta(a_vals, b_vals)
    t, p = welch_t(a_vals, b_vals)

    lines.append(f"- A_384KB={a_mean:.1f}, B_384KB={b_mean:.1f}, A−B={a_b_diff:.1f}\n")
    lines.append(f"- Cliff's δ = {cd:.3f} (threshold > 0.5)\n")
    if p is not None:
        lines.append(f"- Welch t = {t:.2f}, p = {p:.4f} (threshold < 0.01)\n")
        h9_pass = a_b_diff > 0 and p < 0.01 and cd > 0.5
    else:
        lines.append(f"- Welch t = {t:.2f}, p = N/A (scipy not available)\n")
        h9_pass = a_b_diff > 0 and cd > 0.5

    if h9_pass:
        lines.append("- **H9: PASSES** — Prefetcher effect confirmed independent of LLC capacity.\n")
        lines.append("  The A−B difference under L2-fit proves the MSR 0x1A4 control isolates a real mechanism.\n")
    elif a_b_diff > 0:
        lines.append("- **H9: FAILS** — A > B but effect size or significance below threshold.\n")
    else:
        lines.append("- **H9: FAILS** — A ≤ B under L2-fit; prefetcher effect absent.\n")

    return "".join(lines)


def check_l2_size() -> bool:
    """Verify L2 cache size on victim CPU."""
    try:
        result = subprocess.run(
            ["getconf", "LEVEL2_CACHE_SIZE"],
            capture_output=True, text=True, timeout=5
        )
        l2_bytes = int(result.stdout.strip())
        if l2_bytes < WSS_L2FIT:
            log(f"WARNING: L2 cache ({l2_bytes // 1024} KB) < WSS ({WSS_L2FIT // 1024} KB)")
            return False
        log(f"L2 cache: {l2_bytes // 1024} KB (WSS {WSS_L2FIT // 1024} KB fits: YES)")
        return True
    except Exception:
        log("WARNING: could not verify L2 size; assuming 2 MB (sufficient for 384 KB)")
        return True


def write_report(all_rows: List[Dict], l2_check: Dict, out_path: Path):
    by_cond: Dict[str, List[float]] = {}
    for r in all_rows:
        by_cond.setdefault(r["condition"], []).append(r["cycles_per_load"])

    with open(out_path, "w") as f:
        f.write("# Phase 4-NEW Report — L2-Fit Victim Control\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Experimental Parameters\n\n")
        f.write(f"- Victim WSS: {WSS_L2FIT // 1024} KB (L2-fit)\n")
        f.write(f"- Aggressor core counts: A={PHASE2_CORES['A']}, B={PHASE2_CORES['B']}\n")
        f.write(f"- n = {N_TRIALS} trials per condition\n")
        f.write(f"- run_sec = {RUN_SEC}\n\n")

        f.write("## L2 Residency Verification\n\n")
        if l2_check:
            total = sum(l2_check.values())
            if total > 0:
                for ev, cnt in l2_check.items():
                    f.write(f"- {ev}: {cnt:,} ({cnt/total*100:.1f}%)\n")
                l2_hit = l2_check.get("mem_load_retired.l2_hit", 0)
                if total > 0 and l2_hit / total >= 0.80:
                    f.write("- **L2 residency: CONFIRMED** (≥80% L2 hits)\n")
                else:
                    f.write("- **WARNING: L2 residency < 80%** — WSS may not fit cleanly in L2\n")
            else:
                f.write("- Cache event counts unavailable (perf may need additional permissions)\n")
        else:
            f.write("- L2 residency check not run or returned no data\n")

        f.write("\n## Results Summary\n\n")
        f.write("| Cond | n | Mean cycles/load | Std | Median |\n")
        f.write("|------|---|-----------------|-----|--------|\n")
        for cond in ["Q", "A", "B"]:
            vals = by_cond.get(cond, [])
            if not vals:
                f.write(f"| {cond} | 0 | NO DATA |\n")
                continue
            mean = statistics.mean(vals)
            std  = statistics.stdev(vals) if len(vals) > 1 else 0
            med  = statistics.median(vals)
            f.write(f"| {cond} | {len(vals)} | {mean:.1f} | {std:.1f} | {med:.1f} |\n")

        f.write("\n")
        if "Q" in by_cond and "A" in by_cond and "B" in by_cond:
            f.write(eval_h8_h9(by_cond["Q"], by_cond["A"], by_cond["B"]))
        else:
            f.write("## H8/H9 — CANNOT EVALUATE (missing conditions)\n")

        f.write("\n## Universality Verdict (UV1–UV4 components)\n\n")
        q_vals = by_cond.get("Q", [])
        a_vals = by_cond.get("A", [])
        b_vals = by_cond.get("B", [])
        if q_vals and a_vals and b_vals:
            tax_384 = (statistics.mean(a_vals) - statistics.mean(q_vals)) / statistics.mean(q_vals)
            tax_32  = (PHASE2_A - PHASE2_Q) / PHASE2_Q
            reduction = 1.0 - (tax_384 / tax_32) if tax_32 > 0 else float("nan")

            if tax_384 >= 0.10 and reduction < 0.50:
                uv1 = "UV1: SUPPORTED — SF back-invalidation is the primary mechanism"
            elif tax_384 >= 0.10 and reduction >= 0.50:
                uv1 = "UV1: MIXED — Tax present but LLC capacity contributes ≥50%"
            else:
                uv1 = "UV1: NOT SUPPORTED — Tax absent under L2-fit"

            cd_ab = cliff_delta(a_vals, b_vals)
            if cd_ab > 0.5:
                uv2 = "UV2: SUPPORTED — Prefetcher amplification confirmed at L2 level"
            else:
                uv2 = "UV2: NOT SUPPORTED — Prefetcher effect absent at L2 level"

            f.write(f"- {uv1}\n")
            f.write(f"- {uv2}\n")
            f.write(f"- UV3: Requires Phase 5-NEW (SNC isolation) — N/A if SNC disabled\n")
            f.write(f"- UV4: Requires Phase 6-NEW (True WC mapping)\n")
        else:
            f.write("- Verdict deferred (missing data)\n")

    log(f"Wrote: {out_path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 4-NEW: L2-Fit Victim Control ===")
    log(f"Victim WSS: {WSS_L2FIT // 1024} KB (L2-fit)")

    # Verify L2 size
    check_l2_size()

    # L2 residency check (quiescent)
    log("Measuring L2 residency under quiescent conditions...")
    l2_check = measure_l2_residency()

    all_rows: List[Dict] = []

    # Q — quiescent baseline (0 aggressors)
    log("\nRunning Q (quiescent, 0 aggressors)...")
    q_rows = run_condition("Q", n_aggr=0)
    if q_rows:
        all_rows.extend(q_rows)
        runner.save_raw(q_rows, tag="04_new_Q_l2fit")
    runner.cooldown_sleep(3.0)

    # A and B — Phase 2 core counts
    for condition in ["A", "B"]:
        n_aggr = PHASE2_CORES[condition]
        log(f"\nRunning {condition} (n_aggr={n_aggr})...")
        rows = run_condition(condition, n_aggr=n_aggr)
        if rows:
            all_rows.extend(rows)
            runner.save_raw(rows, tag=f"04_new_{condition}_l2fit")
        runner.cooldown_sleep(3.0)

    # Write CSV
    csv_path = PROC_DIR / "04_new_l2fit_matrix.csv"
    fieldnames = ["condition", "n_aggr_cores", "wss_bytes",
                  "trial", "cycles_per_load", "total_loads", "elapsed_sec"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Wrote: {csv_path}")

    write_report(all_rows, l2_check, PROC_DIR / "04_new_l2fit_report.md")

    print(f"\nPhase 4-NEW complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
