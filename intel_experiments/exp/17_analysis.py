#!/usr/bin/env python3
"""
Phase 17.4 — Partial Correlation Analysis and H13–H15 Verdict

Reads results/processed/17_sf_saturation.csv and evaluates:

  H13: R32 SF eviction rate ≥ 10× Phase 12 baseline (45 K/s → ≥ 450 K/s)
  H14: r_sf > 0.5 (p < 0.01) at 384 KB WSS after controlling for l3_miss + mc_queue
  H15: S32 SF rate ≤ 20% of R32; victim tax ≥ 50% eliminated at 384 KB WSS

Uses the same partial correlation framework as Phase 13 (exp/13_partial_correlation.py):
  - Spearman rank-based correlation (Shapiro-Wilk test on residuals)
  - Bootstrap 95% CI (10,000 resamples) when normality fails
  - Welch t-test for pairwise comparisons
  - Cliff's delta for effect size

DR2 threshold for H14: |r_sf| > 0.5 (stricter than Phase 13's |r| > 0.3,
per PHASE17_PROTOCOL.md §2 H14 rationale).

Outputs:
  results/processed/17_sf_saturation_report.md   (human-readable H13–H15 verdict)
  results/processed/17_sf_saturation.csv          (unchanged; this script reads it)
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

DATA_PATH   = PROC_DIR / "17_sf_saturation.csv"
REPORT_PATH = PROC_DIR / "17_sf_saturation_report.md"

SF_BASELINE       = 45_000.0   # Phase 12 Q condition SF/s
H13_THRESHOLD     = 10 * SF_BASELINE   # ≥ 450 K/s
H13_FALSIFIER     = 5  * SF_BASELINE   # < 5× = falsified
H14_R_THRESHOLD   = 0.5        # stricter than Phase 13's 0.3
H15_SF_REDUCTION  = 0.80       # S32 must be ≤ 20% of R32 SF rate
H15_TAX_REDUCTION = 0.50       # ≥ 50% of tax eliminated

WSS_384KB = 393216   # 384 KB rounded to 2 MB boundary → stored as 393216
WSS_32MB  = 33554432


def load_data(path: Path) -> List[Dict]:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "condition":    row["condition"],
                    "n_cores":      int(row["n_aggressor_cores"]),
                    "sf_bypass":    int(row.get("sf_bypass", 0)),
                    "wss_bytes":    int(row["wss_bytes"]),
                    "trial":        int(row["trial"]),
                    "cycles":       float(row["victim_cycles_per_load"]),
                    "sf_evict":     float(row["sf_evictions_per_sec"]),
                    "sf_victim":    float(row.get("sf_victim_per_sec", 0)),
                    "llc_m":        float(row.get("llc_victims_m_per_sec", 0)),
                    "l3_miss":      float(row["l3_misses_per_sec"]),
                    "l2_hit_rate":  float(row["l2_hit_rate"]),
                    "mc_queue":     float(row["mc_queue_occ"]),
                    "imc_bw":       float(row["aggregate_bw_gbps"]),
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


def welch_t(a: List[float], b: List[float]) -> Tuple[float, float]:
    if not HAS_SCIPY:
        return float("nan"), float("nan")
    result = sp_stats.ttest_ind(a, b, equal_var=False)
    return float(result.statistic), float(result.pvalue)


def cliffs_delta(a: List[float], b: List[float]) -> float:
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return float("nan")
    count = sum(1 if ai > bj else (-1 if ai < bj else 0)
                for ai in a for bj in b)
    return count / (n1 * n2)


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


def center(x: List[float]) -> List[float]:
    if not x:
        return x
    m = statistics.mean(x)
    return [xi - m for xi in x]


def ols_residuals(y: List[float], *covariates: List[float]) -> List[float]:
    """Return OLS residuals of y after regressing out covariates."""
    if not HAS_NUMPY:
        # Manual OLS via statistics: project out each covariate sequentially
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
    """
    Compute partial correlation r(y, x | covariates).
    Uses Spearman rank-based if normality rejected on residuals.
    Returns (r, p_value, method_flag) where method_flag=0=Pearson, 1=Spearman.
    """
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
        else:
            # Manual Spearman via rank
            n = len(res_y)
            rank_y = sorted(range(n), key=lambda i: res_y[i])
            rank_x = sorted(range(n), key=lambda i: res_x[i])
            ry = [0.0] * n; rx = [0.0] * n
            for rank, idx in enumerate(rank_y): ry[idx] = rank + 1
            for rank, idx in enumerate(rank_x): rx[idx] = rank + 1
            r = statistics.correlation(ry, rx)
            return r, float("nan"), 1
    else:
        if HAS_SCIPY:
            r, p = sp_stats.pearsonr(res_y, res_x)
            return float(r), float(p), 0
        else:
            r = statistics.correlation(res_y, res_x)
            return r, float("nan"), 0


def bootstrap_partial_r(y, x, covariates, n=10_000) -> Tuple[float, float]:
    """Bootstrap 95% CI for partial correlation coefficient."""
    rng = random.Random(99999)
    n_obs = len(y)
    samples = []
    data = list(zip(y, x, *covariates))
    cov_count = len(covariates)
    for _ in range(n):
        samp = rng.choices(data, k=n_obs)
        sy  = [d[0] for d in samp]
        sx  = [d[1] for d in samp]
        scov = [[d[2 + i] for d in samp] for i in range(cov_count)]
        r, _, _ = partial_correlation(sy, sx, *scov)
        if not math.isnan(r):
            samples.append(r)
    samples.sort()
    if len(samples) < 100:
        return float("nan"), float("nan")
    lo = int(0.025 * len(samples))
    hi = int(0.975 * len(samples))
    return samples[lo], samples[hi]


def fmt_r(r: float, ci_lo: float, ci_hi: float, p: float) -> str:
    r_s   = f"{r:+.3f}" if not math.isnan(r) else "nan"
    ci_s  = (f"[{ci_lo:+.3f}, {ci_hi:+.3f}]"
             if not math.isnan(ci_lo) else "N/A")
    p_s   = f"{p:.4f}" if not math.isnan(p) and p >= 0.0001 else "<0.0001"
    return f"r={r_s} 95%CI={ci_s} p={p_s}"


def evaluate_h13(rows_384: List[Dict], rows_32m: List[Dict]) -> Tuple[str, str]:
    r32_384 = subset(rows_384, "R32")
    if not r32_384:
        return "INSUFFICIENT DATA", "No R32 trials at 384 KB"

    sf_vals = [r["sf_evict"] for r in r32_384]
    mean_sf = statistics.mean(sf_vals)
    ratio   = mean_sf / SF_BASELINE

    ci_lo, ci_hi = bootstrap_ci(sf_vals, statistics.mean)
    verdict = ("CONFIRMED" if mean_sf >= H13_THRESHOLD else
               "FALSIFIED" if mean_sf < H13_FALSIFIER else
               "MARGINAL (between 5×–10× threshold)")

    detail = (f"R32 SF eviction rate (384 KB): {mean_sf:.0f}/s "
              f"({ratio:.1f}× baseline {SF_BASELINE:.0f}/s)\n"
              f"  95% CI: [{ci_lo:.0f}, {ci_hi:.0f}]/s\n"
              f"  Threshold ≥ {H13_THRESHOLD:.0f}/s (10×); "
              f"falsifier < {H13_FALSIFIER:.0f}/s (5×)\n"
              f"  H13: **{verdict}**")
    return verdict, detail


def evaluate_h14(rows_384: List[Dict]) -> Tuple[str, str]:
    active = [r for r in rows_384 if r["condition"] in ("R16", "R24", "R32")]
    if len(active) < 10:
        return "INSUFFICIENT DATA", f"Only {len(active)} active-condition rows at 384 KB"

    y    = [r["cycles"]   for r in active]
    x_sf = [r["sf_evict"] for r in active]
    x_l3 = [r["l3_miss"]  for r in active]
    x_mc = [r["mc_queue"] for r in active]

    r_sf, p_sf, method = partial_correlation(y, x_sf, x_l3, x_mc)
    ci_lo, ci_hi = bootstrap_partial_r(y, x_sf, [x_l3, x_mc])

    _, sw_p = shapiro_wilk(ols_residuals(y, x_l3, x_mc))
    method_s = "Spearman" if method == 1 else "Pearson"

    abs_r = abs(r_sf) if not math.isnan(r_sf) else 0.0
    p_ok  = (p_sf < 0.01) if not math.isnan(p_sf) else False

    if math.isnan(r_sf):
        verdict = "INSUFFICIENT DATA"
    elif abs_r > H14_R_THRESHOLD and p_ok:
        verdict = "CONFIRMED"
    elif abs_r > 0.3 and p_ok:
        verdict = "PARTIAL (|r| > 0.3 but ≤ 0.5)"
    else:
        verdict = "FALSIFIED"

    detail = (f"Partial correlation at 384 KB (R16+R24+R32, n={len(active)}):\n"
              f"  method: {method_s} (Shapiro-Wilk residuals p={sw_p:.4f})\n"
              f"  r_sf (cycles ~ sf_evict | l3_miss, mc_queue) = "
              f"{fmt_r(r_sf, ci_lo, ci_hi, p_sf)}\n"
              f"  Threshold |r| > {H14_R_THRESHOLD} AND p < 0.01\n"
              f"  H14: **{verdict}**")
    return verdict, detail


def evaluate_h15(rows_384: List[Dict]) -> Tuple[str, str]:
    r32 = subset(rows_384, "R32")
    s32 = subset(rows_384, "S32")
    q   = subset(rows_384, "Q")

    if not r32 or not s32 or not q:
        missing = [c for c, v in [("R32", r32), ("S32", s32), ("Q", q)] if not v]
        return "INSUFFICIENT DATA", f"Missing conditions at 384 KB: {missing}"

    r32_sf   = statistics.mean(r["sf_evict"] for r in r32)
    s32_sf   = statistics.mean(r["sf_evict"] for r in s32)
    q_cyc    = statistics.mean(r["cycles"]   for r in q)
    r32_cyc  = statistics.mean(r["cycles"]   for r in r32)
    s32_cyc  = statistics.mean(r["cycles"]   for r in s32)

    sf_ratio     = s32_sf / r32_sf if r32_sf > 0 else float("nan")
    sf_reduction = 1.0 - sf_ratio if not math.isnan(sf_ratio) else float("nan")

    tax_r32  = r32_cyc - q_cyc
    tax_s32  = s32_cyc - q_cyc
    if abs(tax_r32) > 0.1:
        tax_elim = 1.0 - (tax_s32 / tax_r32)
    else:
        tax_elim = float("nan")

    t_stat, p_val = welch_t([r["cycles"] for r in r32],
                            [r["cycles"] for r in s32])
    delta = cliffs_delta([r["cycles"] for r in r32],
                         [r["cycles"] for r in s32])

    sf_pass  = sf_reduction >= H15_SF_REDUCTION if not math.isnan(sf_reduction) else False
    tax_pass = tax_elim >= H15_TAX_REDUCTION if not math.isnan(tax_elim) else False

    if math.isnan(sf_reduction) or math.isnan(tax_elim):
        verdict = "INSUFFICIENT DATA"
    elif sf_pass and tax_pass:
        verdict = "CONFIRMED"
    elif not sf_pass and not tax_pass:
        verdict = "FALSIFIED (both SF and tax criteria failed)"
    elif not sf_pass:
        verdict = f"FALSIFIED (SF reduction {sf_reduction*100:.1f}% < 80%)"
    else:
        verdict = f"FALSIFIED (tax elimination {tax_elim*100:.1f}% < 50%)"

    detail = (f"H15 at 384 KB WSS:\n"
              f"  SF eviction rates: R32={r32_sf:.0f}/s, S32={s32_sf:.0f}/s\n"
              f"  SF reduction: {sf_reduction*100:.1f}% "
              f"(threshold ≥ {H15_SF_REDUCTION*100:.0f}%)\n"
              f"  Victim cycles: Q={q_cyc:.1f}, R32={r32_cyc:.1f}, S32={s32_cyc:.1f}\n"
              f"  Tax (R32−Q): {tax_r32:.1f} cyc; Tax (S32−Q): {tax_s32:.1f} cyc\n"
              f"  Tax eliminated: {tax_elim*100:.1f}% "
              f"(threshold ≥ {H15_TAX_REDUCTION*100:.0f}%)\n"
              f"  R32 vs S32: t={t_stat:.2f} p={p_val:.4f} Cliff δ={delta:+.3f}\n"
              f"  H15: **{verdict}**")
    return verdict, detail


def update_universality_verdict(h13: str, h14: str, h15: str):
    """Determine the narrative for UNIVERSALITY_VERDICT.md update."""
    all_conf   = all(v == "CONFIRMED" for v in [h13, h14, h15])
    h13_fail   = "FALSIFIED" in h13
    h14_fail   = "FALSIFIED" in h14 or "PARTIAL" in h14
    h15_fail   = "FALSIFIED" in h15

    if all_conf:
        return ("All confirm: STREAMING's H2 clause empirically validated on Intel SPR "
                "in the saturation regime. Composed-universality claim is fully supported.")
    elif not h13_fail and (h14_fail or h15_fail):
        return ("H13 confirms (SF can be saturated), but H14/H15 partially falsify. "
                "SF saturation is detectable but not the binding latency mechanism, "
                "or the proxy does not cleanly isolate the H2 clause.")
    elif h13_fail:
        return ("H13 falsifies: Intel SPR SF cannot be saturated under the tested "
                "random-access workload scale. Universality claim is architectural-only.")
    else:
        return "Mixed outcome — see detailed H13/H14/H15 breakdown above."


def main():
    if not DATA_PATH.exists():
        sys.exit(f"ERROR: {DATA_PATH} not found. Run exp/17_sf_saturation.py first.")

    log("=== Phase 17.4: Analysis ===")

    all_rows = load_data(DATA_PATH)
    log(f"  Loaded {len(all_rows)} rows from {DATA_PATH}")

    rows_384 = [r for r in all_rows if r["wss_bytes"] <= WSS_384KB + 1024]
    rows_32m = [r for r in all_rows if r["wss_bytes"] >= WSS_32MB - 1024]

    log(f"  384 KB WSS rows: {len(rows_384)}, 32 MB WSS rows: {len(rows_32m)}")

    h13_verdict, h13_detail = evaluate_h13(rows_384, rows_32m)
    h14_verdict, h14_detail = evaluate_h14(rows_384)
    h15_verdict, h15_detail = evaluate_h15(rows_384)
    narrative = update_universality_verdict(h13_verdict, h14_verdict, h15_verdict)

    with open(REPORT_PATH, "w") as f:
        f.write("# Phase 17 — SF Saturation Report: H13–H15 Verdict\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n")
        f.write("## Platform: Intel Xeon Platinum 8462Y+ (SPR), socket 0\n\n")
        f.write("---\n\n")

        f.write("## Summary Table\n\n")
        f.write("| H# | Claim | Verdict |\n")
        f.write("|----|-------|---------|\n")
        f.write(f"| H13 | R32 SF eviction rate ≥ 10× baseline at 384 KB | **{h13_verdict}** |\n")
        f.write(f"| H14 | r_sf > 0.5 after controlling for l3_miss + mc_queue | **{h14_verdict}** |\n")
        f.write(f"| H15 | S32 SF rate ≤ 20% of R32; tax eliminated ≥ 50% | **{h15_verdict}** |\n")
        f.write("\n")

        f.write("## Universality Verdict for Phase 17\n\n")
        f.write(f"{narrative}\n\n")

        f.write("---\n\n")
        f.write("## H13 — SF Saturation Under 32-Core Random-Access Load\n\n")
        f.write(h13_detail + "\n\n")

        f.write("## H14 — SF Partial Correlation with Victim Latency\n\n")
        f.write(h14_detail + "\n\n")

        f.write("## H15 — STREAMING-Proxy (CLDEMOTE) Mitigation\n\n")
        f.write(h15_detail + "\n\n")

        # Per-condition SF rate table
        f.write("## SF Eviction Rate Progression (384 KB WSS)\n\n")
        f.write("| Condition | n | Mean SF/s | Std SF/s | Ratio to Q |\n")
        f.write("|-----------|---|-----------|----------|------------|\n")
        q_sf = None
        for cond in ["Q", "R16", "R24", "R32", "S32"]:
            rows = subset(rows_384, cond)
            if not rows:
                continue
            sf_vals = [r["sf_evict"] for r in rows]
            m = statistics.mean(sf_vals)
            s = statistics.stdev(sf_vals) if len(sf_vals) > 1 else 0.0
            if cond == "Q":
                q_sf = m
            ratio = f"{m / q_sf:.1f}×" if q_sf and q_sf > 0 else "—"
            f.write(f"| {cond} | {len(rows)} | {m:.0f} | {s:.0f} | {ratio} |\n")

        f.write("\n")

        # Victim cycles table
        f.write("## Victim Cycles Summary (384 KB WSS)\n\n")
        f.write("| Condition | n | Mean cyc/load | Tax vs Q | L2_hit rate |\n")
        f.write("|-----------|---|---------------|----------|-------------|\n")
        q_cyc = None
        for cond in ["Q", "R16", "R24", "R32", "S32"]:
            rows = subset(rows_384, cond)
            if not rows:
                continue
            cyc_vals = [r["cycles"] for r in rows]
            m   = statistics.mean(cyc_vals)
            l2h = statistics.mean(r["l2_hit_rate"] for r in rows)
            if cond == "Q":
                q_cyc = m
            tax = f"+{(m - q_cyc) / q_cyc * 100:.1f}%" if q_cyc else "—"
            f.write(f"| {cond} | {len(rows)} | {m:.1f} | {tax} | {l2h*100:.1f}% |\n")

        f.write("\n")

        # Data completeness check
        f.write("## Data Completeness\n\n")
        checks = {
            "victim_cycles (> 0)": any(r["cycles"] > 0 for r in all_rows),
            "sf_evictions (> 0)":  any(r["sf_evict"] > 0 for r in all_rows),
            "sf_victim_per_sec":   any(r["sf_victim"] > 0 for r in all_rows),
            "l3_misses (> 0)":     any(r["l3_miss"] > 0 for r in all_rows),
            "mc_queue (> 0)":      any(r["mc_queue"] > 0 for r in all_rows),
            "imc_bw (> 0)":        any(r["imc_bw"] > 0 for r in all_rows),
        }
        for k, v in checks.items():
            f.write(f"- {k}: {'PRESENT' if v else 'MISSING — see PMU_SUBSTITUTIONS.md'}\n")

        f.write("\n")
        f.write("## Protocol Reference\n\n")
        f.write("See `PHASE17_PROTOCOL.md` for pre-registered hypotheses and thresholds.\n")
        f.write("Update `UNIVERSALITY_VERDICT.md` with Phase 17 results per §Outputs.\n")

    log(f"Wrote: {REPORT_PATH}")

    # Console summary
    print(f"\n{'='*60}")
    print("Phase 17 H13–H15 Verdict:")
    print(f"  H13 (SF saturation):        {h13_verdict}")
    print(f"  H14 (SF partial corr):      {h14_verdict}")
    print(f"  H15 (proxy mitigation):     {h15_verdict}")
    print(f"\n  Narrative: {narrative}")
    print(f"{'='*60}")
    print(f"\nReport: {REPORT_PATH}")
    print("Update UNIVERSALITY_VERDICT.md and FINDINGS.md per the narrative above.")


if __name__ == "__main__":
    main()
