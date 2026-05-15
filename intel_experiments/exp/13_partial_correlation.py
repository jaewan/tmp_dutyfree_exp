#!/usr/bin/env python3
"""
Phase 13 — Partial Correlation Analysis (DR1/DR2 reframe selection)

Reads Phase 12 full-PMU data and computes:
  r_sf  = partialcorr(victim_cycles, sf_evictions | llc_victims)
  r_llc = partialcorr(victim_cycles, llc_victims  | sf_evictions)
  r_mlp = partialcorr(victim_cycles, mc_queue_occ | sf, llc)

Applies DR2 decision rules to select paper reframe (A, B, C, or D).

DR2 rules:
  If |r_sf| > 0.3 AND p<0.05 → Reframe B (both directory and LLC contribute)
  If |r_sf| ≤ 0.3 AND |r_llc| > 0.3 AND p<0.05 → Reframe A (LLC only)
  If |r_mlp| > max(|r_sf|, |r_llc|) → Reframe C (memory-controller queueing)
  If none reach |r| > 0.3 → Reframe D (escalate to human)

Uses scipy.stats for partial correlation via linear regression residuals.
Tests normality of residuals with Shapiro-Wilk; uses rank-based if non-normal.

Outputs:
  results/processed/13_reframe_decision.md
"""

import sys
import csv
import math
import statistics
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

DATA_PATH   = PROC_DIR / "12_full_pmu_matched.csv"
REPORT_PATH = PROC_DIR / "13_reframe_decision.md"


def load_data(path: Path) -> List[Dict]:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                rows.append({
                    "condition":    row["condition"],
                    "n_cores":      int(row["n_aggressor_cores"]),
                    "cycles":       float(row["victim_cycles_per_load"]),
                    "sf_evict":     float(row["sf_evictions_per_sec"]),
                    "llc_victims":  float(row["llc_victims_per_sec"]),
                    "l3_miss":      float(row["l3_misses_per_sec"]),
                    "imc_bw":       float(row["aggregate_bw_gbps"]),
                    "mc_queue":     float(row["mc_queue_occ"]),
                })
            except (ValueError, KeyError):
                pass
    return rows


def center(x: List[float]) -> List[float]:
    m = statistics.mean(x)
    return [xi - m for xi in x]


def dot(a: List[float], b: List[float]) -> float:
    return sum(ai * bi for ai, bi in zip(a, b))


def ols_residuals(y: List[float], *predictors) -> List[float]:
    """OLS residuals of y ~ predictors (includes intercept implicitly via centering)."""
    # Center all variables
    y_c = center(y)
    preds_c = [center(list(p)) for p in predictors]

    # Build X matrix (each predictor is a column)
    n = len(y_c)
    k = len(preds_c)

    if k == 0:
        return y_c

    if HAS_NUMPY:
        X = np.column_stack(preds_c)
        betas = np.linalg.lstsq(X, y_c, rcond=None)[0]
        fitted = X @ betas
        return (np.array(y_c) - fitted).tolist()
    else:
        # Manual single-predictor case
        if k == 1:
            p = preds_c[0]
            ss_p = dot(p, p)
            if ss_p < 1e-12:
                return y_c
            beta = dot(y_c, p) / ss_p
            return [yi - beta * pi for yi, pi in zip(y_c, p)]
        else:
            # For multiple predictors without numpy: use Gram-Schmidt
            # (simplified; works for k=2)
            e = list(preds_c[0])
            ss = dot(e, e)
            if ss < 1e-12:
                e = [0.0] * n
            for j in range(1, k):
                proj = dot(preds_c[j], e) / max(dot(e, e), 1e-12)
                preds_c[j] = [preds_c[j][i] - proj * e[i] for i in range(n)]
            betas = [dot(y_c, p) / max(dot(p, p), 1e-12) for p in preds_c]
            fitted = [sum(betas[j] * preds_c[j][i] for j in range(k)) for i in range(n)]
            return [y_c[i] - fitted[i] for i in range(n)]


def pearson_r(x: List[float], y: List[float]) -> Tuple[float, float, int]:
    """Returns (r, p_two_tailed, df)."""
    n = len(x)
    if n < 3:
        return (0.0, 1.0, 0)
    if HAS_SCIPY:
        r, p = sp_stats.pearsonr(x, y)
    else:
        # Manual Pearson r
        xc, yc = center(x), center(y)
        ss_x = math.sqrt(dot(xc, xc))
        ss_y = math.sqrt(dot(yc, yc))
        if ss_x < 1e-12 or ss_y < 1e-12:
            return (0.0, 1.0, n - 2)
        r = dot(xc, yc) / (ss_x * ss_y)
        r = max(-1.0, min(1.0, r))
        df = n - 2
        if abs(r) >= 1.0:
            p = 0.0
        else:
            t_stat = r * math.sqrt(df) / math.sqrt(1 - r * r)
            if HAS_SCIPY:
                p = 2 * sp_stats.t.sf(abs(t_stat), df)
            else:
                p = min(1.0, max(0.0, 2 * (1 - _t_cdf(abs(t_stat), df))))
    df = n - 2
    return (float(r), float(p), df)


def _t_cdf(t: float, df: float) -> float:
    """Approximate t-distribution CDF using regularized incomplete beta."""
    x = df / (df + t * t)
    # Simple approximation using normal for large df
    if df > 30:
        import math
        return sp_stats.norm.cdf(t) if HAS_SCIPY else 0.5 * (1 + math.erf(t / math.sqrt(2)))
    if HAS_SCIPY:
        return sp_stats.t.cdf(t, df)
    return 0.5  # fallback


def partial_corr(y: List[float], x: List[float],
                 *controls) -> Tuple[float, float, int, float]:
    """
    Partial correlation of y with x, controlling for *controls.
    Returns (r_partial, p_two_tailed, df, t_stat).
    """
    y_res = ols_residuals(y, *controls)
    x_res = ols_residuals(x, *controls)
    r, p, _ = pearson_r(y_res, x_res)
    n = len(y)
    df = n - 2 - len(controls)
    if df < 1:
        return (r, 1.0, df, 0.0)
    if abs(r) >= 1.0:
        t_stat = math.copysign(1e9, r)
        return (r, 0.0, df, t_stat)
    t_stat = r * math.sqrt(df) / math.sqrt(max(1 - r * r, 1e-12))
    if HAS_SCIPY:
        p = float(2 * sp_stats.t.sf(abs(t_stat), df))
    else:
        p = 1.0  # fallback without scipy
    return (r, p, df, t_stat)


def ci_95(r: float, n: int, n_controls: int) -> Tuple[float, float]:
    """Fisher z-transform 95% CI for partial correlation."""
    df = n - 2 - n_controls
    if df < 3 or abs(r) >= 1.0:
        return (r, r)
    z = math.atanh(r)
    se = 1.0 / math.sqrt(df - 1)
    z_crit = 1.96
    lo = math.tanh(z - z_crit * se)
    hi = math.tanh(z + z_crit * se)
    return (round(lo, 4), round(hi, 4))


def apply_dr2(r_sf: float, p_sf: float,
              r_llc: float, p_llc: float,
              r_mlp: float) -> Tuple[str, str]:
    """Apply DR2 rules; returns (reframe_label, reframe_text)."""
    abs_sf  = abs(r_sf)
    abs_llc = abs(r_llc)
    abs_mlp = abs(r_mlp)

    if abs_mlp > max(abs_sf, abs_llc):
        return ("C",
                "The matched-BW A>B effect is driven by memory-controller queueing, "
                "not LLC or directory pressure.")

    if abs_sf > 0.3 and p_sf < 0.05:
        return ("B",
                "Both directory pressure and LLC capacity contribute; the directory "
                "component is detectable at L3-scale victim WSS but not at L2-fit.")

    if abs_sf <= 0.3 and abs_llc > 0.3 and p_llc < 0.05:
        return ("A",
                "On Intel SPR, the prefetcher amplifies LLC capacity contention; "
                "directory mechanism is not detectable. The AMD result depends on "
                "architectural LLC isolation that Intel does not provide.")

    return ("D", "No signal reaches |r| > 0.3; escalate to human reviewer.")


def shapiro_test(x: List[float]) -> Tuple[float, float]:
    """Shapiro-Wilk normality test; returns (W, p)."""
    if HAS_SCIPY and len(x) >= 3:
        stat, p = sp_stats.shapiro(x)
        return float(stat), float(p)
    return (0.0, 1.0)


def main():
    if not DATA_PATH.exists():
        sys.exit(f"ERROR: {DATA_PATH} not found. Run Phase 12 first.")

    rows = load_data(DATA_PATH)
    # Exclude quiescent for mechanism analysis (no aggressor signal)
    active = [r for r in rows if r["condition"] != "Q"]

    if len(active) < 10:
        sys.exit(f"ERROR: only {len(active)} active rows; need Phase 12 data.")

    log(f"Loaded {len(active)} active rows from Phase 12 data")

    # Extract arrays
    Y     = [r["cycles"]    for r in active]
    X_sf  = [r["sf_evict"]  for r in active]
    X_llc = [r["llc_victims"] for r in active]
    X_mlp = [r["mc_queue"]  for r in active]
    X_bw  = [r["imc_bw"]    for r in active]
    n = len(Y)

    # Check if key events were collected
    sf_nonzero  = sum(1 for x in X_sf  if x > 0)
    llc_nonzero = sum(1 for x in X_llc if x > 0)
    mlp_nonzero = sum(1 for x in X_mlp if x > 0)

    log(f"Non-zero SF: {sf_nonzero}/{n}, LLC: {llc_nonzero}/{n}, MLP: {mlp_nonzero}/{n}")

    # Shapiro-Wilk on victim cycles residuals
    Y_res_all = ols_residuals(Y, X_bw)
    sw_W, sw_p = shapiro_test(Y_res_all)
    use_rank = (sw_p < 0.05)
    log(f"Shapiro-Wilk on Y residuals: W={sw_W:.4f}, p={sw_p:.4f}; "
        f"{'using rank-based correlation' if use_rank else 'using parametric correlation'}")

    if use_rank and HAS_SCIPY:
        # Use Spearman rank-based partial correlation
        def rank_partial(y, x, *controls):
            from scipy.stats import rankdata
            y_res = ols_residuals(list(rankdata(y)), *[list(rankdata(c)) for c in controls])
            x_res = ols_residuals(list(rankdata(x)), *[list(rankdata(c)) for c in controls])
            r, p = sp_stats.pearsonr(y_res, x_res)
            df = n - 2 - len(controls)
            t = r * math.sqrt(df) / math.sqrt(max(1 - r*r, 1e-12))
            p2 = float(2 * sp_stats.t.sf(abs(t), df))
            return float(r), p2, df, float(t)
        pcorr = rank_partial
    else:
        pcorr = partial_corr

    # DR1: compute partial correlations
    # r_sf = partialcorr(cycles, sf | llc_victims)
    r_sf, p_sf, df_sf, t_sf = pcorr(Y, X_sf, X_llc)
    ci_sf = ci_95(r_sf, n, 1)

    # r_llc = partialcorr(cycles, llc | sf_evictions)
    r_llc, p_llc, df_llc, t_llc = pcorr(Y, X_llc, X_sf)
    ci_llc = ci_95(r_llc, n, 1)

    # r_mlp = partialcorr(cycles, mc_queue | sf, llc)
    r_mlp, p_mlp, df_mlp, t_mlp = pcorr(Y, X_mlp, X_sf, X_llc)
    ci_mlp = ci_95(r_mlp, n, 2)

    log(f"r_sf={r_sf:.3f} p={p_sf:.4f} t={t_sf:.2f} df={df_sf}")
    log(f"r_llc={r_llc:.3f} p={p_llc:.4f} t={t_llc:.2f} df={df_llc}")
    log(f"r_mlp={r_mlp:.3f} p={p_mlp:.4f} t={t_mlp:.2f} df={df_mlp}")

    # Apply DR2
    reframe_label, reframe_text = apply_dr2(r_sf, p_sf, r_llc, p_llc, r_mlp)
    log(f"Reframe: {reframe_label} — {reframe_text}")

    # Write report
    with open(REPORT_PATH, "w") as f:
        f.write("# Phase 13 — Partial Correlation Analysis and Reframe Decision\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")
        f.write(f"- n (active trials, excluding Q): {n}\n")
        f.write(f"- Normality test (Shapiro-Wilk on residuals): W={sw_W:.4f}, p={sw_p:.4f}\n")
        f.write(f"- Correlation method: {'rank-based (Spearman)' if use_rank else 'parametric (Pearson)'}\n\n")

        f.write("## DR1 Partial Correlation Results\n\n")
        f.write("| Partial corr | r | 95% CI | t | df | p | |r|>0.3 | p<0.05 |\n")
        f.write("|-------------|---|--------|---|----|----|------|-------|\n")
        f.write(f"| r_sf (cycles ~ sf | llc) | {r_sf:+.4f} | "
                f"[{ci_sf[0]:.4f}, {ci_sf[1]:.4f}] | {t_sf:+.2f} | "
                f"{df_sf} | {p_sf:.4f} | "
                f"{'YES' if abs(r_sf) > 0.3 else 'NO'} | "
                f"{'YES' if p_sf < 0.05 else 'NO'} |\n")
        f.write(f"| r_llc (cycles ~ llc | sf) | {r_llc:+.4f} | "
                f"[{ci_llc[0]:.4f}, {ci_llc[1]:.4f}] | {t_llc:+.2f} | "
                f"{df_llc} | {p_llc:.4f} | "
                f"{'YES' if abs(r_llc) > 0.3 else 'NO'} | "
                f"{'YES' if p_llc < 0.05 else 'NO'} |\n")
        f.write(f"| r_mlp (cycles ~ mlp | sf, llc) | {r_mlp:+.4f} | "
                f"[{ci_mlp[0]:.4f}, {ci_mlp[1]:.4f}] | {t_mlp:+.2f} | "
                f"{df_mlp} | {p_mlp:.4f} | "
                f"{'YES' if abs(r_mlp) > 0.3 else 'NO'} | "
                f"{'YES' if p_mlp < 0.05 else 'NO'} |\n")

        f.write("\n## DR2 Decision\n\n")
        f.write(f"**Reframe selected: {reframe_label}**\n\n")
        f.write(f"**Reframe text (verbatim from DR2):** {reframe_text}\n\n")

        f.write("### DR2 Branch Evaluation\n\n")
        f.write(f"- |r_mlp| > max(|r_sf|, |r_llc|)? "
                f"{'YES → Reframe C' if abs(r_mlp) > max(abs(r_sf), abs(r_llc)) else 'NO'}\n")
        f.write(f"- |r_sf| > 0.3 AND p_sf < 0.05? "
                f"{'YES → Reframe B' if abs(r_sf) > 0.3 and p_sf < 0.05 else 'NO'}\n")
        f.write(f"- |r_sf| ≤ 0.3 AND |r_llc| > 0.3 AND p_llc < 0.05? "
                f"{'YES → Reframe A' if abs(r_sf) <= 0.3 and abs(r_llc) > 0.3 and p_llc < 0.05 else 'NO'}\n")
        f.write(f"- None of the above → Reframe D (escalate)\n\n")

        f.write("### Observations Beyond the Reframe\n\n")
        f.write("Summary statistics by condition:\n\n")
        f.write("| Cond | n | Cycles | SF evict/s | LLC vic/s | L3 miss/s | MC queue |\n")
        f.write("|------|---|--------|-----------|----------|----------|----------|\n")
        for cond in ["A", "B"]:
            cr = [r for r in active if r["condition"] == cond]
            if not cr:
                continue
            f.write(f"| {cond} | {len(cr)} | "
                    f"{statistics.mean(r['cycles'] for r in cr):.1f} | "
                    f"{statistics.mean(r['sf_evict'] for r in cr):.0f} | "
                    f"{statistics.mean(r['llc_victims'] for r in cr):.0f} | "
                    f"{statistics.mean(r['l3_miss'] for r in cr):.0f} | "
                    f"{statistics.mean(r['mc_queue'] for r in cr):.0f} |\n")

        if sf_nonzero < n // 2:
            f.write("\n**WARNING:** SF eviction events were 0 for many trials. "
                    "r_sf may be unreliable. See PMU_SUBSTITUTIONS.md.\n")
        if llc_nonzero < n // 2:
            f.write("\n**WARNING:** LLC victim events were 0 for many trials. "
                    "r_llc may be unreliable.\n")

    log(f"Wrote: {REPORT_PATH}")
    print(f"\nPhase 13 complete: {REPORT_PATH}")
    print(f"  Reframe: {reframe_label} — {reframe_text}")


if __name__ == "__main__":
    main()
