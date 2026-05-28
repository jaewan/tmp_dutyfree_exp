#!/usr/bin/env python3
"""
plot_mitigation.py — Phase 5 ASPLOS synthesis plot for the Mitigation Trap Plan.

Concatenates the per-trial CSVs from Phase 1~3 and plots the BW–slowdown plane:
  X = aggregate_bw_gbps  (CXL bandwidth)
  Y = victim_slowdown_pct
Each phase/knob is drawn as its own series. The "missing mode" (Streaming) would
sit bottom-right (high BW, low tax); WB baseline sits top-right, WC bottom-left,
and the CAT/MBA sweeps trace a curve between them.

Inputs (any that exist):
  results/processed/20_phase1_prefetch.csv
  results/processed/21_phase2_cat.csv
  results/processed/22_phase1c_tlb.csv
  results/processed/23_phase3_mba.csv
Output:
  figures/mitigation_synthesis.pdf
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).parent.parent
PROC = PROJECT_ROOT / "results" / "processed"
FIGS = PROJECT_ROOT / "figures"

CSV_FILES = [
    ("Phase1 prefetch/sw", "20_phase1_prefetch.csv"),
    ("Phase1c TLB",        "22_phase1c_tlb.csv"),
    ("Phase2 CAT",         "21_phase2_cat.csv"),
    ("Phase3 MBA",         "23_phase3_mba.csv"),
]


def load(path: Path):
    pts = defaultdict(lambda: {"bw": [], "tax": []})
    with open(path) as f:
        for r in csv.DictReader(f):
            try:
                bw  = float(r["aggregate_bw_gbps"])
                tax = float(r["victim_slowdown_pct"])
            except (ValueError, KeyError):
                continue
            label = f"{r.get('knob','')}={r.get('knob_value','')}"
            pts[label]["bw"].append(bw)
            pts[label]["tax"].append(tax)
    return pts


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def main():
    FIGS.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 6))

    markers = ["o", "s", "^", "D", "v", "P", "X"]
    any_data = False
    for mi, (series_name, fname) in enumerate(CSV_FILES):
        path = PROC / fname
        if not path.exists():
            print(f"  skip (missing): {fname}")
            continue
        pts = load(path)
        if not pts:
            continue
        any_data = True
        xs = [mean(d["bw"]) for d in pts.values()]
        ys = [mean(d["tax"]) for d in pts.values()]
        ax.scatter(xs, ys, label=series_name, marker=markers[mi % len(markers)], s=80, alpha=0.8)
        for label, d in pts.items():
            ax.annotate(label, (mean(d["bw"]), mean(d["tax"])),
                        fontsize=6, alpha=0.6,
                        xytext=(4, 4), textcoords="offset points")

    if not any_data:
        sys.exit("No Phase 1~3 CSVs found in results/processed/. Run the phases first.")

    ax.set_xlabel("Aggregate CXL bandwidth (GB/s)")
    ax.set_ylabel("Victim slowdown (%)")
    ax.set_title("Mitigation Trap: bandwidth vs. victim tax\n"
                 "(missing Streaming mode = bottom-right: high BW, low tax)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    out = FIGS / "mitigation_synthesis.pdf"
    fig.tight_layout()
    fig.savefig(out)
    fig.savefig(FIGS / "mitigation_synthesis.png", dpi=120)
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
