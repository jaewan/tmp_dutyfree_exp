#!/usr/bin/env python3
"""
Plot: Four-Condition Matrix — victim latency by condition.
Bar chart with error bars (mean ± 1 std), individual trial overlays.

Output: results/figures/02_matrix.pdf
"""

import sys
import csv
import statistics
from pathlib import Path

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError:
    sys.exit("pip3 install matplotlib numpy")

PROC_DIR    = Path(__file__).parent.parent / "results" / "processed"
FIGURES_DIR = Path(__file__).parent.parent / "results" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

CONDITION_ORDER = ["Q", "A", "B", "C", "D"]
CONDITION_LABELS = {
    "Q": "Quiescent",
    "A": "WB+pf\n(Cond. A)",
    "B": "WB-nopf\n(Cond. B)",
    "C": "WC/NT\n(Cond. C)",
    "D": "MOVNTDQA\n(Cond. D)",
}
COLORS = {
    "Q": "#888888",
    "A": "#d62728",   # red  — high pressure
    "B": "#ff7f0e",   # orange — medium
    "C": "#1f77b4",   # blue  — low
    "D": "#e377c2",   # pink — advisory
}


def load_data():
    csv_path = PROC_DIR / "02_matrix.csv"
    if not csv_path.exists():
        sys.exit(f"ERROR: {csv_path} not found")
    data = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            c = row["condition"]
            data.setdefault(c, []).append(float(row["cycles_per_load"]))
    return data


def plot_matrix(data: dict, out_path: Path):
    conds = [c for c in CONDITION_ORDER if c in data]
    means   = [statistics.mean(data[c]) for c in conds]
    stds    = [statistics.stdev(data[c]) if len(data[c]) > 1 else 0 for c in conds]
    ns      = [len(data[c]) for c in conds]
    colors  = [COLORS[c] for c in conds]
    labels  = [CONDITION_LABELS[c] for c in conds]

    fig, ax = plt.subplots(figsize=(7, 4.5))

    x = np.arange(len(conds))
    bars = ax.bar(x, means, yerr=stds, color=colors, alpha=0.85,
                  capsize=4, error_kw={"linewidth": 1.5},
                  edgecolor="black", linewidth=0.7)

    # Overlay individual trial points
    jitter_rng = np.random.default_rng(42)
    for i, c in enumerate(conds):
        jitter = jitter_rng.uniform(-0.18, 0.18, size=len(data[c]))
        ax.scatter(x[i] + jitter, data[c],
                   s=6, color="black", alpha=0.35, zorder=5, linewidths=0)

    # Annotate n= under each bar
    for i, (n, m) in enumerate(zip(ns, means)):
        ax.text(x[i], 5, f"n={n}", ha="center", va="bottom",
                fontsize=7, color="dimgray")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Victim latency (cycles per dependent load)", fontsize=10)
    ax.set_xlabel("Aggressor condition", fontsize=10)
    ax.set_title("Directory Tax on Intel Sapphire Rapids\n"
                 "Victim pointer-chase latency under four streaming conditions",
                 fontsize=10)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    ax.set_axisbelow(True)

    # Annotate % increase over Q
    q_mean = statistics.mean(data.get("Q", [0]))
    for i, (c, m) in enumerate(zip(conds, means)):
        if c == "Q" or q_mean == 0:
            continue
        pct = (m - q_mean) / q_mean * 100
        sign = "+" if pct >= 0 else ""
        ax.text(x[i], m + stds[i] + 2,
                f"{sign}{pct:.0f}%", ha="center", va="bottom",
                fontsize=8, color="black")

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    print(f"Wrote: {out_path}")

    # Also write a text caption
    caption_path = out_path.with_suffix(".caption.txt")
    with open(caption_path, "w") as f:
        f.write("Figure X: Victim pointer-chase latency (cycles per dependent load, "
                "random-walk 32 MB working set) under four aggressor conditions "
                "on Intel Sapphire Rapids (Xeon Platinum 8462Y+, socket 0, DDR5-4800). "
                "Bars show mean ± 1 standard deviation; dots show individual trials "
                f"(n={min(ns)}–{max(ns)} per condition). "
                "Condition labels: Q=quiescent; "
                "A=WB streaming with hardware prefetcher ON (MSR 0x1A4=0x0); "
                "B=WB streaming with hardware prefetcher OFF (MSR 0x1A4=0xF); "
                "C=NT loads (MOVNTDQA); D=NT loads on WB region (advisory hint). "
                "Percentage annotations show increase vs. quiescent baseline. "
                "All conditions matched to the same aggregate bandwidth target.")
    print(f"Wrote: {caption_path}")


def main():
    data = load_data()
    out_path = FIGURES_DIR / "02_matrix.pdf"
    plot_matrix(data, out_path)


if __name__ == "__main__":
    main()
