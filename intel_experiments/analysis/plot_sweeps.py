#!/usr/bin/env python3
"""
Plot: WSS sweep and aggressor count sweep (Phase 4).

Usage:
  python3 analysis/plot_sweeps.py --wss        → figures/04_wss.pdf
  python3 analysis/plot_sweeps.py --aggressor  → figures/04_aggressors.pdf
  python3 analysis/plot_sweeps.py              → both
"""

import sys
import csv
import argparse
import statistics
from pathlib import Path
from collections import defaultdict

try:
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    sys.exit("pip3 install matplotlib numpy")

PROC_DIR    = Path(__file__).parent.parent / "results" / "processed"
FIGURES_DIR = Path(__file__).parent.parent / "results" / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)


def plot_wss():
    csv_path = PROC_DIR / "04_wss_sweep.csv"
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found — skipping WSS plot")
        return

    by_wss = defaultdict(list)
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            wss_mb = int(row["wss_bytes"]) / (1024 * 1024)
            by_wss[wss_mb].append(float(row["tax_pct"]))

    wss_vals  = sorted(by_wss.keys())
    tax_means = [statistics.mean(by_wss[w]) for w in wss_vals]
    tax_stds  = [statistics.stdev(by_wss[w]) if len(by_wss[w]) > 1 else 0
                 for w in wss_vals]

    fig, ax = plt.subplots(figsize=(6, 3.5))
    ax.errorbar(wss_vals, tax_means, yerr=tax_stds,
                fmt="o-", color="#d62728", capsize=4, linewidth=2, markersize=7)
    ax.axhline(0, color="gray", linestyle="--", linewidth=1)
    ax.axvline(2, color="orange", linestyle=":", linewidth=1, label="L2 boundary (~2 MB)")
    ax.axvline(60, color="blue", linestyle=":", linewidth=1, label="LLC capacity (~60 MB)")
    ax.set_xscale("log", base=2)
    ax.set_xlabel("Victim working-set size (MB, log₂ scale)", fontsize=10)
    ax.set_ylabel("Latency tax vs. quiescent (%)", fontsize=10)
    ax.set_title("Directory Tax vs. Victim WSS\n(Condition A, Intel SPR)", fontsize=10)
    ax.legend(fontsize=8)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    out_path = FIGURES_DIR / "04_wss.pdf"
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    print(f"Wrote: {out_path}")


def plot_aggressors():
    csv_path = PROC_DIR / "05_aggressor_sweep.csv"
    if not csv_path.exists():
        print(f"WARNING: {csv_path} not found — skipping aggressor sweep plot")
        return

    by_n = defaultdict(list)
    by_n_bw = defaultdict(list)
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            n = int(row["n_aggr_cores"])
            by_n[n].append(float(row["tax_pct"]))
            by_n_bw[n].append(float(row["aggregate_bw_gbps"]))

    n_vals    = sorted(by_n.keys())
    tax_means = [statistics.mean(by_n[n]) for n in n_vals]
    tax_stds  = [statistics.stdev(by_n[n]) if len(by_n[n]) > 1 else 0 for n in n_vals]
    bw_means  = [statistics.mean(by_n_bw[n]) for n in n_vals]

    fig, ax1 = plt.subplots(figsize=(6, 3.5))
    ax2 = ax1.twinx()

    ax1.errorbar(n_vals, tax_means, yerr=tax_stds,
                 fmt="o-", color="#d62728", capsize=4, linewidth=2, markersize=7,
                 label="Latency tax (%)")
    ax2.plot(n_vals, bw_means, "s--", color="#1f77b4", linewidth=1.5, markersize=5,
             label="Agg BW (GB/s)")

    ax1.set_xlabel("Number of aggressor cores", fontsize=10)
    ax1.set_ylabel("Latency tax vs. quiescent (%)", color="#d62728", fontsize=10)
    ax2.set_ylabel("Aggregate streaming bandwidth (GB/s)", color="#1f77b4", fontsize=10)
    ax1.set_title("Directory Tax vs. Aggressor Count\n(Condition A, 32 MB victim WSS, Intel SPR)",
                  fontsize=10)
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, fontsize=8)
    ax1.yaxis.grid(True, linestyle="--", alpha=0.4)
    fig.tight_layout()
    out_path = FIGURES_DIR / "04_aggressors.pdf"
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    print(f"Wrote: {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wss", action="store_true")
    parser.add_argument("--aggressor", action="store_true")
    args = parser.parse_args()

    run_both = not args.wss and not args.aggressor
    if args.wss or run_both:
        plot_wss()
    if args.aggressor or run_both:
        plot_aggressors()


if __name__ == "__main__":
    main()
