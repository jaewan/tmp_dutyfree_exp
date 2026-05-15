#!/usr/bin/env python3
"""
Phase 18 -- Forced-Turnover SF Saturation Analysis: H16-H18 Verdict

Reads results/processed/18_sf_forced_turnover.csv and evaluates:

  H16: R32 SF eviction rate >= 10x Phase 12 baseline (45 K/s -> >= 450 K/s)
  H17: |r_sf| > 0.5 (p < 0.01) at 384 KB WSS after controlling for
       l3_miss + mc_queue (Spearman if Shapiro-Wilk rejects normality)
  H18: mean victim cycles at R32 (384 KB) >= Q * 1.10 (+10% degradation)
       p < 0.05 (Mann-Whitney U) — positive test for SF back-inval pathway

H17 interpretation note: evaluated only if H16 passes. If victim cycles are
flat (range < 2%), H17 result is labeled "degenerate variance" per H14 precedent
and NOT treated as evidence for the SF pathway.

H18 interpretation note: evaluated only if H16 passes. The 384 KB victim WSS
keeps the victim fully L2-resident (L2_HIT=100% expected). Any observed victim
degradation propagates via SF back-invalidation evicting victim L2 lines.
If H16 passes but H18 fails: SF saturation is not sufficient for victim
degradation on Intel SPR -- robust architectural negative.

Outputs:
  results/processed/18_sf_forced_turnover_report.md
"""

import sys
import csv
import math
import statistics
import random
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from runner import log, RESULTS_PROC

try:
    import scipy.stats as sp_stats
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    log("WARNING: scipy not available; using manual partial correlation")

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

PROC_DIR = RESULTS_PROC

DATA_PATH   = PROC_DIR / "18_sf_forced_turnover.csv"
REPORT_PATH = PROC_DIR / "18_sf_forced_turnover_report.md"

SF_BASELINE     = 45_000.0   # Phase 12 Q condition SF/s
H16_THRESHOLD   = 10 * SF_BASELINE   # >= 450 K/s
H16_FALSIFIER   =  5 * SF_BASELINE   # < 5x = falsified
H17_R_THRESHOLD = 0.5
H18_THRESHOLD   = 1.10   # >= 10% increase
H18_FALSIFIER   = 1.05   # < 5% = falsified; between 5-10% = MARGINAL

WSS_384KB = 393216    # stored value for 384 KB condition
WSS_32MB  = 33554432


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_data(path: Path) -> List[Dict]:
    rows = []
    with open(path) as f:
        for row in csv.DictReader(f):
            try:
                rows.append({
                    "condition":   row["condition"],
                    "n_cores":     int(row["n_aggressor_cores"]),
                    "pf_disabled": int(row.get("pf_disabled", 1)),
                    "wss_bytes":   int(row["wss_bytes"]),
                    "trial":       int(row["trial"]),
                    "cycles":      float(row["victim_cycles_per_load"]),
                    "sf_evict":    float(row["sf_evictions_per_sec"]),
                    "sf_victim":   float(row.get("sf_victim_per_sec", 0)),
                    "l3_miss":     float(row["l3_misses_per_sec"]),
                    "l2_hit_rate": float(row["l2_hit_rate"]),
                    "mc_queue":    float(row["mc_queue_occ"]),
                    "imc_bw":      float(row["aggregate_bw_gbps"]),
                })
            except (ValueError, KeyError) as e:
                log(f"  Skipping row: {e}")
    return rows


def subset(rows: List[Dict], condition: Optional[str] = None,
           wss: Optional[int] = None) -> List[Dict]:
    out = rows
    if condition is not None:
        out = [r for r in out if r["condition"] == condition]
    if wss is not None:
        out = [r for r in out if r["wss_bytes"] == wss]
    return out


# ---------------------------------------------------------------------------
# Statistics helpers (identical to Phase 17 analysis)
# ---------------------------------------------------------------------------

def shapiro_wilk(x: List[float]) -> Tuple[float, float]:
    if not HAS_SCIPY or len(x) < 3:
        return float("nan"), float("nan")
    w, p = sp_stats.shapiro(x)
    return float(w), float(p)


def bootstrap_ci(x: List[float], func=statistics.mean,
                 n_resamples: int = 10_000, ci: float = 0.95) -> Tuple[float, float]:
    if not x:
        return float("nan"), float("nan")
    rng = random.Random(12345)
    samples = [func(rng.choices(x, k=len(x))) for _ in range(n_resamples)]
    samples.sort()
    lo = int((1 - ci) / 2 * n_resamples)
    hi = int((1 + ci) / 2 * n_resamples)
    return samples[lo], samples[min(hi, n_resamples - 1)]


def ols_residuals(y: List[float], *covariates: List[float]) -> List[float]:
    if not HAS_NUMPY:
        resid = list(y)
        for c in covariates:
            cy = statistics.mean(resid)
            cc = statistics.mean(c)
            num = sum((yi - cy) * (ci - cc) for yi, ci in zip(resid, c))
            den = sum((ci - cc) ** 2 for ci in c)
            if den == 0:
                continue
            b = num / den
            a = cy - b * cc
            resid = [yi - (a + b * ci) for yi, ci in zip(resid, c)]
        return resid
    y_arr = np.array(y)
    X = np.column_stack([np.ones(len(y))] + [np.array(c) for c in covariates])
    beta, _, _, _ = np.linalg.lstsq(X, y_arr, rcond=None)
    return list(y_arr - X @ beta)


def partial_correlation(y: List[float], x: List[float],
                        *covariates: List[float]) -> Tuple[float, float, float]:
    if len(y) < 5:
        return float("nan"), float("nan"), 0
    res_y = ols_residuals(y, *covariates)
    res_x = ols_residuals(x, *covariates)
    _, sw_p = shapiro_wilk(res_y)
    use_rank = sw_p < 0.05
    if use_rank:
        if HAS_SCIPY:
            r, p = sp_stats.spearmanr(res_y, res_x)
            return float(r), float(p), 1
        n = len(res_y)
        rank_y = sorted(range(n), key=lambda i: res_y[i])
        rank_x = sorted(range(n), key=lambda i: res_x[i])
        ry = [0.0] * n; rx = [0.0] * n
        for rank, i in enumerate(rank_y): ry[i] = rank + 1
        for rank, i in enumerate(rank_x): rx[i] = rank + 1
        return statistics.correlation(ry, rx), float("nan"), 1
    if HAS_SCIPY:
        r, p = sp_stats.pearsonr(res_y, res_x)
        return float(r), float(p), 0
    return statistics.correlation(res_y, res_x), float("nan"), 0


def bootstrap_partial_r(y, x, covariates, n: int = 10_000) -> Tuple[float, float]:
    rng     = random.Random(99999)
    n_obs   = len(y)
    samples = []
    data    = list(zip(y, x, *covariates))
    n_cov   = len(covariates)
    for _ in range(n):
        samp = rng.choices(data, k=n_obs)
        sy   = [d[0] for d in samp]
        sx   = [d[1] for d in samp]
        scov = [[d[2 + i] for d in samp] for i in range(n_cov)]
        r, _, _ = partial_correlation(sy, sx, *scov)
        if not math.isnan(r):
            samples.append(r)
    samples.sort()
    if len(samples) < 100:
        return float("nan"), float("nan")
    return samples[int(0.025 * len(samples))], samples[int(0.975 * len(samples))]


def fmt_r(r: float, ci_lo: float, ci_hi: float, p: float) -> str:
    r_s  = f"{r:+.3f}" if not math.isnan(r) else "nan"
    ci_s = (f"[{ci_lo:+.3f}, {ci_hi:+.3f}]" if not math.isnan(ci_lo) else "N/A")
    p_s  = f"{p:.4f}" if not math.isnan(p) and p >= 0.0001 else "<0.0001"
    return f"r={r_s} 95%CI={ci_s} p={p_s}"


def mann_whitney_u(a: List[float], b: List[float]) -> Tuple[float, float]:
    if not HAS_SCIPY:
        return float("nan"), float("nan")
    result = sp_stats.mannwhitneyu(a, b, alternative="greater")
    return float(result.statistic), float(result.pvalue)


# ---------------------------------------------------------------------------
# Hypothesis evaluations
# ---------------------------------------------------------------------------

def evaluate_h16(rows_384: List[Dict]) -> Tuple[str, str]:
    r32 = subset(rows_384, "R32")
    if not r32:
        return "INSUFFICIENT DATA", "No R32 trials at 384 KB"

    sf_vals = [r["sf_evict"] for r in r32]
    mean_sf = statistics.mean(sf_vals)
    ratio   = mean_sf / SF_BASELINE
    ci_lo, ci_hi = bootstrap_ci(sf_vals, statistics.mean)

    verdict = ("CONFIRMED" if mean_sf >= H16_THRESHOLD else
               "FALSIFIED" if mean_sf <  H16_FALSIFIER else
               "MARGINAL (between 5x-10x threshold)")

    detail = (f"R32 SF eviction rate (384 KB): {mean_sf:.0f}/s "
              f"({ratio:.1f}x baseline {SF_BASELINE:.0f}/s)\n"
              f"  95% CI: [{ci_lo:.0f}, {ci_hi:.0f}]/s\n"
              f"  Threshold >= {H16_THRESHOLD:.0f}/s (10x); "
              f"falsifier < {H16_FALSIFIER:.0f}/s (5x)\n"
              f"  H16: **{verdict}**")
    return verdict, detail


def evaluate_h17(rows_384: List[Dict], h16_verdict: str) -> Tuple[str, str]:
    if "FALSIFIED" in h16_verdict:
        return ("N/A (H16 failed)",
                "H17 not evaluated: SF did not saturate (H16 falsified). "
                "No SF-driven latency pathway to test.")

    active = [r for r in rows_384 if r["condition"] in ("R8", "R16", "R24", "R32")]
    if len(active) < 10:
        return "INSUFFICIENT DATA", f"Only {len(active)} active rows at 384 KB"

    y    = [r["cycles"]   for r in active]
    x_sf = [r["sf_evict"] for r in active]
    x_l3 = [r["l3_miss"]  for r in active]
    x_mc = [r["mc_queue"] for r in active]

    r_sf, p_sf, method = partial_correlation(y, x_sf, x_l3, x_mc)
    ci_lo, ci_hi = bootstrap_partial_r(y, x_sf, [x_l3, x_mc])

    _, sw_p    = shapiro_wilk(ols_residuals(y, x_l3, x_mc))
    method_s   = "Spearman" if method == 1 else "Pearson"

    abs_r = abs(r_sf) if not math.isnan(r_sf) else 0.0
    p_ok  = (p_sf < 0.01) if not math.isnan(p_sf) else False

    # Degenerate-variance check (per H14 precedent)
    cyc_range = max(y) - min(y)
    cyc_mean  = statistics.mean(y)
    degen_pct = cyc_range / cyc_mean * 100 if cyc_mean > 0 else 0.0
    degenerate = degen_pct < 2.0

    if math.isnan(r_sf):
        verdict = "INSUFFICIENT DATA"
    elif abs_r > H17_R_THRESHOLD and p_ok:
        suffix  = " (degenerate variance — not evidence of SF pathway)" if degenerate else ""
        verdict = f"CONFIRMED{suffix}"
    elif abs_r > 0.3 and p_ok:
        verdict = "PARTIAL (|r| > 0.3 but <= 0.5)"
    else:
        verdict = "FALSIFIED"

    detail = (f"Partial correlation at 384 KB (R8+R16+R24+R32, n={len(active)}):\n"
              f"  method: {method_s} (Shapiro-Wilk residuals p={sw_p:.4f})\n"
              f"  r_sf (cycles ~ sf_evict | l3_miss, mc_queue) = "
              f"{fmt_r(r_sf, ci_lo, ci_hi, p_sf)}\n"
              f"  Threshold |r| > {H17_R_THRESHOLD} AND p < 0.01\n"
              f"  Victim cycles range: {cyc_range:.2f} cyc "
              f"({degen_pct:.1f}% of mean {'— DEGENERATE' if degenerate else '— OK'})\n"
              f"  H17: **{verdict}**")
    return verdict, detail


def evaluate_h18(rows_384: List[Dict], h16_verdict: str) -> Tuple[str, str]:
    if "FALSIFIED" in h16_verdict:
        return ("N/A (H16 failed)",
                "H18 not evaluated: SF did not saturate (H16 falsified).")

    r32 = subset(rows_384, "R32")
    q   = subset(rows_384, "Q")
    if not r32 or not q:
        return "INSUFFICIENT DATA", "Missing R32 or Q rows at 384 KB"

    q_cyc   = statistics.mean(r["cycles"] for r in q)
    r32_cyc = statistics.mean(r["cycles"] for r in r32)
    ratio   = r32_cyc / q_cyc if q_cyc > 0 else float("nan")
    tax_pct = (ratio - 1.0) * 100 if not math.isnan(ratio) else float("nan")

    # Mann-Whitney U: R32 > Q (one-sided, alternative="greater")
    mw_stat, mw_p = mann_whitney_u([r["cycles"] for r in r32],
                                    [r["cycles"] for r in q])

    # L2_HIT rate check: should stay near 1.0 if no back-invalidation,
    # or drop if SF back-inval is evicting victim L2 lines
    r32_l2h = statistics.mean(r["l2_hit_rate"] for r in r32)
    q_l2h   = statistics.mean(r["l2_hit_rate"] for r in q)

    p_sig = (mw_p < 0.05) if not math.isnan(mw_p) else False

    if math.isnan(ratio):
        verdict = "INSUFFICIENT DATA"
    elif ratio >= H18_THRESHOLD and p_sig:
        verdict = "CONFIRMED"
    elif ratio >= H18_THRESHOLD and not p_sig:
        verdict = "MARGINAL (ratio >= 1.10 but p >= 0.05)"
    elif ratio >= H18_FALSIFIER:
        verdict = f"MARGINAL ({tax_pct:.1f}% increase, threshold 10%, falsifier 5%)"
    else:
        verdict = "FALSIFIED"

    l2h_note = ""
    if r32_l2h < q_l2h - 0.05:
        l2h_note = (f"\n  NOTE: L2_HIT dropped {q_l2h*100:.1f}% -> {r32_l2h*100:.1f}% "
                    f"under R32 — consistent with SF back-inval evicting victim L2 lines")

    detail = (f"H18 at 384 KB WSS (SF pathway isolation):\n"
              f"  Q cycles:   {q_cyc:.2f} (n={len(q)})\n"
              f"  R32 cycles: {r32_cyc:.2f} (n={len(r32)})\n"
              f"  Ratio R32/Q: {ratio:.4f} (tax = {tax_pct:.2f}%)\n"
              f"  Threshold >= {H18_THRESHOLD:.2f}x ({(H18_THRESHOLD-1)*100:.0f}%); "
              f"falsifier < {H18_FALSIFIER:.2f}x\n"
              f"  Mann-Whitney U (R32 > Q): U={mw_stat:.0f} p={mw_p:.4f}\n"
              f"  Victim L2_HIT: Q={q_l2h*100:.1f}% R32={r32_l2h*100:.1f}%"
              f"{l2h_note}\n"
              f"  H18: **{verdict}**")
    return verdict, detail


def universality_narrative(h16: str, h17: str, h18: str) -> str:
    if "FALSIFIED" in h16:
        return ("H16 falsifies: Intel SPR SF cannot be saturated even under forced-turnover "
                "(4 MB/core random-index loads, prefetchers disabled). "
                "STREAMING-H2 on Intel is architecturally applicable but not empirically "
                "triggered under any tested workload. Universality claim remains theoretical.")
    if "CONFIRMED" in h18 and "N/A" not in h18:
        return ("H16 confirms SF saturation; H18 confirms victim degradation at 384 KB WSS. "
                "SF back-invalidation pathway is real on Intel SPR in the saturation regime. "
                "STREAMING-H2 is empirically grounded on Intel SPR.")
    if "N/A" not in h18 and ("FALSIFIED" in h18 or "MARGINAL" in h18):
        return ("H16 confirms SF saturation, but H18 fails: victim latency does NOT increase "
                "at 384 KB WSS even when SF is saturated. SF saturation is not sufficient "
                "for victim degradation on Intel SPR. The SF back-invalidation pathway "
                "does not bind even at architectural saturation — robust negative result. "
                "STREAMING-H2 on Intel addresses a real capacity mechanism but not a "
                "performance-observable victim-latency pathway.")
    return "Mixed outcome — see H16/H17/H18 details above."


def main():
    if not DATA_PATH.exists():
        sys.exit(f"ERROR: {DATA_PATH} not found. Run exp/18_sf_forced_turnover.py first.")

    log("=== Phase 18 Analysis: H16-H18 Verdict ===")

    all_rows = load_data(DATA_PATH)
    log(f"  Loaded {len(all_rows)} rows from {DATA_PATH}")

    rows_384 = [r for r in all_rows if r["wss_bytes"] <= WSS_384KB + 1024]
    rows_32m = [r for r in all_rows if r["wss_bytes"] >= WSS_32MB  - 1024]
    log(f"  384 KB WSS rows: {len(rows_384)}, 32 MB WSS rows: {len(rows_32m)}")

    h16_verdict, h16_detail = evaluate_h16(rows_384)
    h17_verdict, h17_detail = evaluate_h17(rows_384, h16_verdict)
    h18_verdict, h18_detail = evaluate_h18(rows_384, h16_verdict)
    narrative = universality_narrative(h16_verdict, h17_verdict, h18_verdict)

    with open(REPORT_PATH, "w") as f:
        f.write("# Phase 18 -- SF Forced-Turnover Report: H16-H18 Verdict\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n")
        f.write("## Platform: Intel Xeon Platinum 8462Y+ (SPR), socket 0\n\n")
        f.write("---\n\n")

        f.write("## Summary Table\n\n")
        f.write("| H# | Claim | Verdict |\n")
        f.write("|----|-------|---------|\n")
        f.write(f"| H16 | R32 SF eviction rate >= 10x Phase 12 baseline | **{h16_verdict}** |\n")
        f.write(f"| H17 | |r_sf| > 0.5 at 384 KB after covariate control | **{h17_verdict}** |\n")
        f.write(f"| H18 | Victim cycles >= Q * 1.10 at R32, 384 KB WSS | **{h18_verdict}** |\n")
        f.write("\n")

        f.write("## Universality Verdict for Phase 18\n\n")
        f.write(f"{narrative}\n\n")
        f.write("---\n\n")

        f.write("## H16 -- SF Saturation Under Forced-Turnover Load\n\n")
        f.write(h16_detail + "\n\n")

        f.write("## H17 -- SF Partial Correlation with Victim Latency\n\n")
        f.write(h17_detail + "\n\n")

        f.write("## H18 -- Victim Latency Increase Under SF Saturation\n\n")
        f.write(h18_detail + "\n\n")

        # SF rate progression table
        f.write("## SF Eviction Rate Progression (384 KB WSS)\n\n")
        f.write("| Condition | n | Mean SF/s | Std SF/s | Ratio to Q |\n")
        f.write("|-----------|---|-----------|----------|------------|\n")
        q_sf = None
        for cond in ["Q", "R8", "R16", "R24", "R32"]:
            rows = subset(rows_384, cond)
            if not rows:
                continue
            sf_vals = [r["sf_evict"] for r in rows]
            m = statistics.mean(sf_vals)
            s = statistics.stdev(sf_vals) if len(sf_vals) > 1 else 0.0
            if cond == "Q":
                q_sf = m
            ratio = f"{m / q_sf:.1f}x" if q_sf and q_sf > 0 else "--"
            f.write(f"| {cond} | {len(rows)} | {m:.0f} | {s:.0f} | {ratio} |\n")
        f.write("\n")

        # Victim cycles progression table
        f.write("## Victim Cycles Summary (384 KB WSS)\n\n")
        f.write("| Condition | n | Mean cyc/load | Tax vs Q | L2_hit |\n")
        f.write("|-----------|---|---------------|----------|--------|\n")
        q_cyc = None
        for cond in ["Q", "R8", "R16", "R24", "R32"]:
            rows = subset(rows_384, cond)
            if not rows:
                continue
            cyc_vals = [r["cycles"] for r in rows]
            m   = statistics.mean(cyc_vals)
            l2h = statistics.mean(r["l2_hit_rate"] for r in rows)
            if cond == "Q":
                q_cyc = m
            tax = (f"+{(m - q_cyc) / q_cyc * 100:.1f}%"
                   if q_cyc and q_cyc > 0 else "--")
            f.write(f"| {cond} | {len(rows)} | {m:.2f} | {tax} | {l2h*100:.1f}% |\n")
        f.write("\n")

        # 32 MB WSS table (context)
        f.write("## Victim Cycles Summary (32 MB WSS)\n\n")
        f.write("| Condition | n | Mean cyc/load | Tax vs Q |\n")
        f.write("|-----------|---|---------------|----------|\n")
        q_cyc32 = None
        for cond in ["Q", "R8", "R16", "R24", "R32"]:
            rows = subset(rows_32m, cond)
            if not rows:
                continue
            cyc_vals = [r["cycles"] for r in rows]
            m = statistics.mean(cyc_vals)
            if cond == "Q":
                q_cyc32 = m
            tax = (f"+{(m - q_cyc32) / q_cyc32 * 100:.1f}%"
                   if q_cyc32 and q_cyc32 > 0 else "--")
            f.write(f"| {cond} | {len(rows)} | {m:.1f} | {tax} |\n")
        f.write("\n")

        f.write("## Protocol Reference\n\n")
        f.write("See `PHASE18_PROTOCOL.md` for pre-registered hypotheses and thresholds.\n")
        f.write("Update `UNIVERSALITY_VERDICT.md` and `FINDINGS.md` with Phase 18 results.\n")

    log(f"Wrote: {REPORT_PATH}")

    print(f"\n{'='*60}")
    print("Phase 18 H16-H18 Verdict:")
    print(f"  H16 (SF saturation):           {h16_verdict}")
    print(f"  H17 (SF partial corr):         {h17_verdict}")
    print(f"  H18 (victim tax under SF sat): {h18_verdict}")
    print(f"\n  Narrative: {narrative}")
    print(f"{'='*60}")
    print(f"\nReport: {REPORT_PATH}")
    print("Update UNIVERSALITY_VERDICT.md and FINDINGS.md with Phase 18 results.")


if __name__ == "__main__":
    main()
