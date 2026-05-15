#!/usr/bin/env python3
"""
Phase 3.5 — Mechanism Analysis: SF Eviction Rate vs. Throughput (Post-Phase 3)

Reads results/processed/03_pmu_sweep.csv and:
  1. Computes linear regression (slope, R², p-value) for sf_evict_rate vs n_aggr_cores
     for each condition (A, B, C).
  2. Tests H5 pre-registered criteria.
  3. Evaluates H12 setup: documents need for LLC miss rate covariate.
  4. Writes results/processed/03_5_mechanism_analysis.md
  5. Generates figures/03_mechanism.pdf

Requires: scipy, statsmodels, matplotlib, pandas
"""

import sys
import csv
import statistics
from pathlib import Path
from datetime import datetime
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log

PROC_DIR = runner.RESULTS_PROC
FIG_DIR  = runner.PROJECT_ROOT / "figures"
PROC_DIR.mkdir(parents=True, exist_ok=True)
FIG_DIR.mkdir(parents=True, exist_ok=True)

CSV_PATH = PROC_DIR / "03_pmu_sweep.csv"
OUT_MD   = PROC_DIR / "03_5_mechanism_analysis.md"
OUT_PDF  = FIG_DIR  / "03_mechanism.pdf"


def load_csv(path: Path) -> List[Dict]:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({
                "condition":      row["condition"],
                "n_aggr_cores":   int(row["n_aggr_cores"]),
                "trial":          int(row["trial"]),
                "agg_bw_gbps":    float(row["agg_bw_gbps"]),
                "sf_evict_total": int(row["sf_evict_total"]),
                "sf_evict_rate":  float(row["sf_evict_rate"]),
                "sf_victim_retry": int(row.get("sf_victim_retry", 0) or 0),
                "tor_drd_pref":   int(row.get("tor_drd_pref", 0) or 0),
            })
    return rows


def linreg(x: List[float], y: List[float]):
    """OLS with HC3 via statsmodels; falls back to scipy if unavailable."""
    try:
        import numpy as np
        import statsmodels.api as sm
        X = sm.add_constant(np.array(x, dtype=float))
        Y = np.array(y, dtype=float)
        model = sm.OLS(Y, X).fit(cov_type="HC3")
        return {
            "slope":    model.params[1],
            "intercept": model.params[0],
            "r2":       model.rsquared,
            "p_slope":  model.pvalues[1],
            "se_slope": model.bse[1],
            "n":        len(x),
        }
    except ImportError:
        pass

    try:
        from scipy import stats
        slope, intercept, r, p, se = stats.linregress(x, y)
        return {
            "slope":    slope,
            "intercept": intercept,
            "r2":       r ** 2,
            "p_slope":  p,
            "se_slope": se,
            "n":        len(x),
        }
    except ImportError:
        log("WARNING: neither statsmodels nor scipy available; regression skipped")
        return None


def analyze_condition(rows: List[Dict], cond: str):
    """Aggregate per core count and fit regression."""
    cond_rows = [r for r in rows if r["condition"] == cond]
    if not cond_rows:
        return None

    by_cores: Dict[int, List[float]] = {}
    for r in cond_rows:
        by_cores.setdefault(r["n_aggr_cores"], []).append(r["sf_evict_rate"])

    core_counts = sorted(by_cores.keys())
    mean_rates  = [statistics.mean(by_cores[c]) for c in core_counts]
    bw_vals     = []
    for c in core_counts:
        bw_for_c = [r["agg_bw_gbps"] for r in cond_rows if r["n_aggr_cores"] == c]
        bw_vals.append(statistics.mean(bw_for_c))

    reg = linreg(bw_vals, mean_rates)
    return {
        "condition":   cond,
        "core_counts": core_counts,
        "mean_rates":  mean_rates,
        "bw_vals":     bw_vals,
        "by_cores":    by_cores,
        "reg":         reg,
        "n_trials":    len(cond_rows),
    }


def eval_h5(results: Dict[str, dict]) -> str:
    lines = []
    lines.append("## H5 Evaluation\n")
    lines.append("**Pre-registered:** A and B slope > 0, R² > 0.85; C slope ≈ 0.\n\n")

    for cond in ["A", "B", "C"]:
        r = results.get(cond)
        if not r or not r["reg"]:
            lines.append(f"- Condition {cond}: NO DATA\n")
            continue
        reg = r["reg"]
        slope = reg["slope"]
        r2    = reg["r2"]
        p     = reg["p_slope"]
        n     = reg["n"]

        if cond in ("A", "B"):
            h5_pass = slope > 0 and r2 > 0.85 and p < 0.01
            verdict = "PASS" if h5_pass else "FAIL"
        else:
            h5_pass = p > 0.1
            verdict = "PASS (slope ≈ 0)" if h5_pass else "FAIL (slope ≠ 0)"

        lines.append(f"- Condition {cond}: slope={slope:.0f} ev/s per GB/s, "
                     f"R²={r2:.3f}, p={p:.4f}, n={n} → **{verdict}**\n")

    return "".join(lines)


def make_plot(results: Dict[str, dict], out_path: Path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        log("matplotlib not available; skipping plot")
        return

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    colors = {"A": "#d62728", "B": "#1f77b4", "C": "#2ca02c"}

    for cond, r in results.items():
        if not r:
            continue
        ax = axes[0]
        bw    = r["bw_vals"]
        rates = r["mean_rates"]
        ax.scatter(bw, rates, color=colors.get(cond, "gray"), label=cond, s=50)
        if r["reg"] and r["reg"]["slope"] is not None:
            x0, x1 = min(bw), max(bw)
            xfit = [x0, x1]
            yfit = [r["reg"]["intercept"] + r["reg"]["slope"] * x for x in xfit]
            ax.plot(xfit, yfit, color=colors.get(cond, "gray"), linestyle="--",
                    alpha=0.6)

    axes[0].set_xlabel("Aggregate BW (GB/s, reported)")
    axes[0].set_ylabel("SF Evict/s (evict_one + evict_gtone)")
    axes[0].set_title("Phase 3: SF Eviction Rate vs. Bandwidth")
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # Per-core sweep plot
    ax2 = axes[1]
    for cond, r in results.items():
        if not r:
            continue
        byw = r["by_cores"]
        xs = sorted(byw.keys())
        means  = [statistics.mean(byw[x]) for x in xs]
        stdevs = [statistics.stdev(byw[x]) if len(byw[x]) > 1 else 0 for x in xs]
        ax2.errorbar(xs, means, yerr=stdevs, label=cond, marker="o",
                     color=colors.get(cond, "gray"), capsize=4)

    ax2.set_xlabel("Aggressor Core Count")
    ax2.set_ylabel("SF Evict/s")
    ax2.set_title("Phase 3: SF Eviction Rate vs. Core Count")
    ax2.legend()
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(str(out_path), dpi=150, bbox_inches="tight")
    log(f"Wrote figure: {out_path}")
    plt.close()


def write_report(results: Dict[str, dict], out_path: Path):
    with open(out_path, "w") as f:
        f.write("# Phase 3.5 — Mechanism Analysis\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Data Summary\n\n")
        f.write("| Cond | n points | n trials | BW range (GB/s) | "
                "SF evict/s mean | SF evict/s std |\n")
        f.write("|------|----------|----------|-----------------|"
                "----------------|----------------|\n")
        for cond in ["A", "B", "C"]:
            r = results.get(cond)
            if not r:
                f.write(f"| {cond} | NO DATA |\n")
                continue
            all_rates = [v for vals in r["by_cores"].values() for v in vals]
            mean_r = statistics.mean(all_rates)
            std_r  = statistics.stdev(all_rates) if len(all_rates) > 1 else 0
            bw_min = min(r["bw_vals"])
            bw_max = max(r["bw_vals"])
            n_pts  = len(r["core_counts"])
            n_tr   = r["n_trials"]
            f.write(f"| {cond} | {n_pts} | {n_tr} | {bw_min:.1f}–{bw_max:.1f} | "
                    f"{mean_r:.0f} | {std_r:.0f} |\n")

        f.write("\n## Linear Regression Results (SF evict/s ~ agg_bw)\n\n")
        f.write("| Cond | Slope (ev/s per GB/s) | SE | R² | p-value |\n")
        f.write("|------|-----------------------|----|----|---------|\n")
        for cond in ["A", "B", "C"]:
            r = results.get(cond)
            if not r or not r["reg"]:
                f.write(f"| {cond} | N/A |\n")
                continue
            reg = r["reg"]
            f.write(f"| {cond} | {reg['slope']:.0f} | {reg['se_slope']:.0f} | "
                    f"{reg['r2']:.3f} | {reg['p_slope']:.4f} |\n")

        f.write("\n")
        f.write(eval_h5(results))

        f.write("\n## H12 Setup Note\n\n")
        f.write("H12 requires victim cycles as dependent variable alongside sf_evict_rate.\n")
        f.write("Phase 3 did not run a concurrent victim; the correlation test requires\n")
        f.write("Phase 4-NEW data where victim cycles and SF rates are measured together.\n")
        f.write("The regression model is:\n")
        f.write("  victim_cycles ~ sf_evict_rate + llc_miss_rate + agg_bw_gbps\n\n")

        f.write("## Notes\n\n")
        f.write("- SF eviction rate is `evict_one + evict_gtone` summed across 32 CHA tiles.\n")
        f.write("- agg_bw_gbps values are as reported by stream_wb (likely underestimated ~16×).\n")
        f.write("  Regression axis should be interpreted as relative BW proxy, not absolute.\n")
        f.write("- High trial-to-trial variance in SF evict/s is expected: perf stat collects\n")
        f.write("  a single window; transient SF pressure bursts affect individual measurements.\n")
        f.write("- See Phase 3.6 for actual bandwidth measurement.\n")
        f.write("- See DIAGNOSIS.md §2 for identifiability context.\n")

    log(f"Wrote: {out_path}")


def main():
    if not CSV_PATH.exists():
        log(f"ERROR: {CSV_PATH} not found. Run Phase 3 first (exp/03_pmu_sweep.py).")
        sys.exit(1)

    log("=== Phase 3.5: Mechanism Analysis ===")
    rows = load_csv(CSV_PATH)
    log(f"Loaded {len(rows)} rows from {CSV_PATH}")

    conditions = sorted(set(r["condition"] for r in rows))
    log(f"Conditions present: {conditions}")

    results = {}
    for cond in conditions:
        results[cond] = analyze_condition(rows, cond)
        if results[cond]:
            reg = results[cond]["reg"]
            if reg:
                log(f"  [{cond}] slope={reg['slope']:.0f} ev/s·GB/s "
                    f"R²={reg['r2']:.3f} p={reg['p_slope']:.4f}")

    write_report(results, OUT_MD)
    make_plot(results, OUT_PDF)

    print(f"\nPhase 3.5 complete.")
    print(f"  Report: {OUT_MD}")
    print(f"  Figure: {OUT_PDF}")


if __name__ == "__main__":
    main()
