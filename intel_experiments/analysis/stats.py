#!/usr/bin/env python3
"""
Phase 5 — Statistical Analysis

For every pairwise comparison from Phase 2:
  - Welch's two-sample t-test (unequal variance)
  - Effect size: Cliff's delta (non-parametric)
  - 95% CI for mean difference (bootstrap, n=10000)
  - Bonferroni correction over C(5,2)=10 comparisons
  - Shapiro-Wilk normality check per sample

For Phase 3 slopes:
  - Linear regression with HC3 robust SEs (statsmodels)

Outputs:
  results/processed/05_stats_table.md   ← main paper table
  results/processed/05_regression.md    ← slope analysis
"""

import sys
import csv
import json
import math
import statistics
import itertools
from pathlib import Path
from typing import List, Tuple, Dict, Optional

try:
    import numpy as np
    from scipy import stats as scipy_stats
    import statsmodels.api as sm
    from statsmodels.stats.weightstats import DescrStatsW
except ImportError:
    sys.exit("Missing dependencies: pip3 install numpy scipy statsmodels")

PROC_DIR = Path(__file__).parent.parent / "results" / "processed"
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_BOOTSTRAP = 10000
ALPHA       = 0.05
CONDITIONS  = ["Q", "A", "B", "C", "D"]
N_PAIRS     = len(list(itertools.combinations(CONDITIONS, 2)))  # C(5,2) = 10
ALPHA_BONF  = ALPHA / N_PAIRS


def load_matrix() -> Dict[str, List[float]]:
    """Load Phase 2 results from CSV; return dict of condition → cycles list."""
    csv_path = PROC_DIR / "02_matrix.csv"
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found. Run exp/02_matrix.py first.")

    data: Dict[str, List[float]] = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            cond = row["condition"]
            val  = float(row["cycles_per_load"])
            data.setdefault(cond, []).append(val)
    return data


def welch_t(a: List[float], b: List[float]) -> Tuple[float, float, float]:
    """Welch's t-test; returns (t, df, p-value)."""
    t, p = scipy_stats.ttest_ind(a, b, equal_var=False)
    n1, n2 = len(a), len(b)
    s1, s2 = np.var(a, ddof=1), np.var(b, ddof=1)
    num = (s1/n1 + s2/n2)**2
    den = (s1/n1)**2/(n1-1) + (s2/n2)**2/(n2-1)
    df = num / den if den > 0 else n1 + n2 - 2
    return float(t), float(df), float(p)


def cliffs_delta(a: List[float], b: List[float]) -> float:
    """Non-parametric effect size: Cliff's delta ∈ [-1, 1]."""
    n_a, n_b = len(a), len(b)
    greater = sum(1 for x in a for y in b if x > y)
    less    = sum(1 for x in a for y in b if x < y)
    return (greater - less) / (n_a * n_b)


def bootstrap_ci(a: List[float], b: List[float],
                 n_boot: int = N_BOOTSTRAP, alpha: float = 0.05) -> Tuple[float, float]:
    """Bootstrap 95% CI for mean difference (a - b)."""
    rng = np.random.default_rng(42)
    a_arr = np.array(a)
    b_arr = np.array(b)
    diffs = np.array([rng.choice(a_arr, len(a_arr), replace=True).mean() -
                      rng.choice(b_arr, len(b_arr), replace=True).mean()
                      for _ in range(n_boot)])
    lo = float(np.percentile(diffs, 100 * alpha / 2))
    hi = float(np.percentile(diffs, 100 * (1 - alpha / 2)))
    return lo, hi


def shapiro_wilk(samples: List[float]) -> Tuple[float, float, bool]:
    """Returns (W, p, is_normal) where is_normal = p > 0.05."""
    if len(samples) < 3:
        return (float("nan"), float("nan"), False)
    w, p = scipy_stats.shapiro(samples)
    return float(w), float(p), bool(p > 0.05)


def summarize(name: str, samples: List[float]) -> Dict:
    arr = np.array(samples)
    sw_w, sw_p, is_normal = shapiro_wilk(samples)
    return {
        "condition": name,
        "n": len(samples),
        "mean": float(arr.mean()),
        "std":  float(arr.std(ddof=1)),
        "median": float(np.median(arr)),
        "iqr":  float(np.percentile(arr, 75) - np.percentile(arr, 25)),
        "min":  float(arr.min()),
        "max":  float(arr.max()),
        "sw_w": sw_w,
        "sw_p": sw_p,
        "normal": is_normal,
    }


def load_pmu_sweep() -> Optional[Dict]:
    """Load Phase 3 PMU sweep data for regression."""
    csv_path = PROC_DIR / "03_pmu_sweep.csv"
    if not csv_path.exists():
        return None
    data: Dict[str, Tuple[List[float], List[float]]] = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            cond = row["condition"]
            bw   = float(row["agg_bw_gbps"])
            sf   = float(row["sf_evict_rate"])
            if cond not in data:
                data[cond] = ([], [])
            data[cond][0].append(bw)
            data[cond][1].append(sf)
    return data


def regression_hc3(x: List[float], y: List[float]) -> Dict:
    """OLS with HC3 robust SEs. Returns slope, SE, p, R²."""
    X = sm.add_constant(np.array(x))
    Y = np.array(y)
    model = sm.OLS(Y, X).fit(cov_type="HC3")
    slope = float(model.params[1])
    se    = float(model.bse[1])
    p     = float(model.pvalues[1])
    r2    = float(model.rsquared)
    return {"slope": slope, "se": se, "p": p, "r2": r2,
            "n": len(x), "intercept": float(model.params[0])}


def write_stats_table(summaries: Dict[str, Dict], pairs: List[Dict], path: Path):
    with open(path, "w") as f:
        f.write("# Table 1 — Statistical Analysis of Four-Condition Matrix\n")
        f.write("## Phase 5 (auto-generated)\n\n")

        f.write("## Descriptive Statistics\n\n")
        f.write("| Cond | n | Mean (cycles) | Std | Median | IQR | Shapiro-W | p_SW | Normal? |\n")
        f.write("|------|---|---------------|-----|--------|-----|-----------|------|---------|\n")
        for cond in CONDITIONS:
            s = summaries.get(cond)
            if not s:
                continue
            f.write(f"| {cond} | {s['n']} | {s['mean']:.2f} | {s['std']:.2f} | "
                    f"{s['median']:.2f} | {s['iqr']:.2f} | {s['sw_w']:.4f} | "
                    f"{s['sw_p']:.4f} | {'yes' if s['normal'] else 'no'} |\n")

        f.write(f"\n## Pairwise Tests (α={ALPHA}, Bonferroni-corrected α'={ALPHA_BONF:.4f})\n\n")
        f.write("| Pair | Δmean (A−B) | 95% CI (lo, hi) | Welch t | df | p | "
                "p_bonf | Sig? | Cliff δ | Magnitude |\n")
        f.write("|------|-------------|-----------------|---------|----|----|"
                "--------|------|---------|----------|\n")
        for pair in pairs:
            p_sig = "✓" if pair["p_bonf"] < ALPHA_BONF else "✗"
            delta_mag = ("large" if abs(pair["cliff_d"]) > 0.474 else
                         "medium" if abs(pair["cliff_d"]) > 0.33 else "small")
            f.write(f"| {pair['a']} vs {pair['b']} | {pair['delta_mean']:+.2f} | "
                    f"({pair['ci_lo']:+.2f}, {pair['ci_hi']:+.2f}) | "
                    f"{pair['t']:.3f} | {pair['df']:.1f} | {pair['p']:.4f} | "
                    f"{pair['p_bonf']:.4f} | {p_sig} | "
                    f"{pair['cliff_d']:+.3f} | {delta_mag} |\n")

        f.write(f"\n*Bonferroni correction over {N_PAIRS} comparisons.*\n")
        f.write("*Cliff's delta: |δ| > 0.474 = large, > 0.33 = medium, > 0.147 = small.*\n\n")

        f.write("## Hypothesis Verdicts\n\n")
        q_mean = summaries.get("Q", {}).get("mean", 0)
        a_mean = summaries.get("A", {}).get("mean", 0)
        b_mean = summaries.get("B", {}).get("mean", 0)
        c_mean = summaries.get("C", {}).get("mean", 0)

        h1 = (a_mean >= q_mean * 1.15) if q_mean else False
        h2 = ((b_mean - q_mean) < 0.5 * (a_mean - q_mean)) if (a_mean > q_mean) else False
        h3 = (c_mean <= q_mean * 1.02) if q_mean else False
        h3_kill = (c_mean > q_mean * 1.05) if q_mean else False

        def a_vs_q_pct():
            return (a_mean - q_mean) / q_mean * 100 if q_mean else 0

        f.write(f"- **H1** (A ≥ Q×1.15): A={a_mean:.1f}, Q={q_mean:.1f}, "
                f"ratio={a_mean/q_mean:.3f}×, Δ={a_vs_q_pct():.1f}% "
                f"→ **{'PASS' if h1 else 'FAIL'}**\n")
        f.write(f"- **H2** (B-Q < 0.5×(A-Q)): Δ_A={a_mean-q_mean:.1f}, "
                f"Δ_B={b_mean-q_mean:.1f} "
                f"→ **{'PASS' if h2 else 'FAIL'}**\n")
        f.write(f"- **H3** (C ≤ Q×1.02): C={c_mean:.1f}, Q={q_mean:.1f} "
                f"→ **{'PASS' if h3 else 'FAIL'}**")
        if h3_kill:
            f.write(" ⚠ PAPER-KILLING: C > Q×1.05")
        f.write("\n")
    print(f"Wrote: {path}")


def write_regression_table(reg: Dict[str, Dict], path: Path):
    with open(path, "w") as f:
        f.write("# Phase 3 — Linear Regression: SF Eviction Rate vs. BW\n\n")
        f.write("OLS with HC3 robust standard errors.\n\n")
        f.write("| Cond | n | slope (evict/s per GB/s) | SE | p | R² | sig? |\n")
        f.write("|------|---|--------------------------|----|----|-----|------|\n")
        for cond, r in reg.items():
            sig = "✓" if r["p"] < 0.05 else "✗"
            f.write(f"| {cond} | {r['n']} | {r['slope']:.1f} | {r['se']:.1f} | "
                    f"{r['p']:.4f} | {r['r2']:.3f} | {sig} |\n")

        f.write("\n**H5 verdict:**\n")
        if "A" in reg and "C" in reg:
            a_slope_sig = reg["A"]["p"] < 0.05 and reg["A"]["slope"] > 0
            c_flat      = reg["C"]["p"] >= 0.05
            f.write(f"- A slope ({reg['A']['slope']:.1f} evict/s per GB/s): "
                    f"{'significant' if a_slope_sig else 'NOT significant'}\n")
            if "B" in reg:
                f.write(f"- B slope ({reg['B']['slope']:.1f} evict/s per GB/s): "
                        f"{'significant' if reg['B']['p'] < 0.05 else 'NOT significant'}\n")
            f.write(f"- C slope ({reg['C']['slope']:.1f} evict/s per GB/s): "
                    f"{'flat (H5 supported)' if c_flat else 'NOT flat (H5 threatened)'}\n")
            if a_slope_sig and c_flat:
                f.write("\n→ H5 **PASS**: SF eviction scales with WB throughput; flat for WC.\n")
            else:
                f.write("\n→ H5 **FAIL** or partial — investigate.\n")
    print(f"Wrote: {path}")


def main():
    data = load_matrix()
    if not data:
        sys.exit("No data loaded from 02_matrix.csv")

    print(f"Loaded conditions: {sorted(data.keys())}")
    for c, samples in data.items():
        print(f"  {c}: n={len(samples)}, mean={statistics.mean(samples):.1f}")

    summaries = {c: summarize(c, samples) for c, samples in data.items()}

    # Pairwise tests
    pairs = []
    conds_present = [c for c in CONDITIONS if c in data]
    for a_cond, b_cond in itertools.combinations(conds_present, 2):
        a_data = data[a_cond]
        b_data = data[b_cond]
        t, df, p = welch_t(a_data, b_data)
        p_bonf = min(p * N_PAIRS, 1.0)
        ci_lo, ci_hi = bootstrap_ci(a_data, b_data)
        cd = cliffs_delta(a_data, b_data)
        pairs.append({
            "a": a_cond,
            "b": b_cond,
            "delta_mean": statistics.mean(a_data) - statistics.mean(b_data),
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "t": t,
            "df": df,
            "p": p,
            "p_bonf": p_bonf,
            "cliff_d": cd,
        })

    stats_path = PROC_DIR / "05_stats_table.md"
    write_stats_table(summaries, pairs, stats_path)

    # Regression on PMU sweep
    pmu_data = load_pmu_sweep()
    if pmu_data:
        reg = {}
        for cond, (bw, sf) in pmu_data.items():
            if len(bw) >= 3:
                reg[cond] = regression_hc3(bw, sf)
        reg_path = PROC_DIR / "05_regression.md"
        write_regression_table(reg, reg_path)
    else:
        print("No Phase 3 PMU data found — skipping regression analysis")

    print(f"\nStatistical analysis complete. Main table: {stats_path}")


if __name__ == "__main__":
    main()
