#!/usr/bin/env python3
"""
Mitigation Trap Plan — Phase 1(a)+(b): Microarchitectural Baselines

Establishes that hardware prefetching is the engine of CXL bandwidth and that
software-only streaming cannot recover it, while measuring the victim tax each
mode imposes.

  (a) MSR Prefetch Tuning: condition A (WB, prefetch ON) vs B (WB, prefetch OFF,
      MSR 0x1A4=0xF). Expect BW to collapse with prefetcher disabled.
  (b) Software Prefetching: condition C (MOVNTDQA) and E (MOVNTDQA, prefetch OFF).
      Expect WC BW << WB BW — no hardware concurrency, no bandwidth.

The WB aggressor (A) is mapped read-only (mprotect PROT_READ) as a Streaming
epoch proxy. Victim is LLC-resident (32 MB ≈ 53% of the 60 MB socket LLC).

Outputs:
  results/processed/20_phase1_prefetch.csv
  results/processed/20_phase1_prefetch_report.md
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log
import mitigation_common as mc

PROC_DIR = runner.RESULTS_PROC

VICTIM_WSS = 32 * 1024 * 1024   # 32 MB, LLC-resident
N_TRIALS   = 30
RUN_SEC    = 2.0
WARMUP     = 8.0
# Under aggressor pressure the victim WSS starts fully evicted; the first ~10
# trials decay from a cold transient (tax ~200% → steady state). Discard that
# many victim trials so measurement begins at steady state (see CXL Phase 1
# diagnosis: outliers were trials 0-9, not random bimodality).
WARMUP_TRIALS = 12
# Fixed aggressor count: 2 cores under-saturate the 60 MB LLC, making the victim
# bimodal (sometimes survives in LLC). 8 cores sweep the LLC reliably so the tax
# is consistently high and low-variance.
N_AGGR_FIXED = 8

# condition -> (knob, label, readonly)
CONDITIONS = [
    ("A", "prefetcher",  "wb_pf_on",   True),   # 1a baseline (read-only epoch)
    ("B", "prefetcher",  "wb_pf_off",  False),  # 1a: prefetch disabled
    ("C", "stream_mode", "wc_movntdqa", False), # 1b: software NT streaming
    ("E", "stream_mode", "wc_nopf",    False),  # 1b: NT streaming, prefetch off
]


def load_n_aggr(default: int = 4) -> int:
    try:
        raw = sorted(runner.RESULTS_RAW.glob("01_calibration_*.json"), reverse=True)
        if raw:
            return json.loads(raw[0].read_text())["core_counts"]["A"]["n_cores"]
    except Exception:
        pass
    return default


def write_report(summary, n_aggr, path: Path):
    import datetime
    with open(path, "w") as f:
        f.write("# Phase 1(a)+(b) Report — Microarchitectural Baselines\n")
        f.write(f"## Date: {datetime.datetime.now().isoformat()}\n")
        f.write(f"## Aggressor cores: {n_aggr}, victim WSS: {VICTIM_WSS>>20} MB\n\n")
        f.write("| condition | label | agg BW (GB/s) | tax % median | tax % mean±sd | n |\n")
        f.write("|-----------|-------|--------------:|-------------:|--------------:|--:|\n")
        for (cond, _knob, label, _ro) in CONDITIONS:
            key = (label, cond)
            if key in summary:
                s = summary[key]
                f.write(f"| {cond} | {label} | {s['bw_mean']:.2f} | "
                        f"{s['tax_median']:.2f} | {s['tax_mean']:.2f} ± {s['tax_std']:.2f} | {s['n']} |\n")
        f.write("\n**Expected:** A BW >> B BW (>=2x, prefetch essential); "
                "C/E BW << A BW (software streaming caps bandwidth).\n")
    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()
    n_aggr = N_AGGR_FIXED
    log(f"=== Phase 1(a)+(b): prefetch & software-streaming baselines "
        f"({n_aggr} aggressor cores) ===")

    q = mc.measure_quiescent(VICTIM_WSS, trials=10, run_sec=RUN_SEC,
                             warmup_trials=3)
    log(f"quiescent baseline: {q:.1f} cycles/load")

    all_rows = []
    for (cond, knob, label, readonly) in CONDITIONS:
        log(f"\n-- condition {cond} ({label}), readonly={readonly} --")
        trials, agg_bw = mc.measure_point(
            cond, n_aggr, victim_wss=VICTIM_WSS, trials=N_TRIALS,
            run_sec=RUN_SEC, warmup=WARMUP, readonly=readonly,
            warmup_trials=WARMUP_TRIALS)
        if not trials:
            log(f"  WARNING: no victim results for condition {cond}; skipping")
            continue
        knob_value = "on" if label == "wb_pf_on" else ("off" if label == "wb_pf_off" else label)
        rows = mc.make_rows(trials, phase="1", knob=knob, knob_value=label,
                            condition=cond, n_aggr_cores=n_aggr, quiescent=q,
                            agg_bw=agg_bw, readonly=readonly, notes=label)
        all_rows.extend(rows)
        import statistics
        log(f"  agg_bw={agg_bw:.1f} GB/s, victim tax mean="
            f"{statistics.mean(r['tax_pct'] for r in rows):.1f}%")

    csv_path = PROC_DIR / "20_phase1_prefetch.csv"
    mc.write_csv(all_rows, csv_path)
    write_report(mc.summarize(all_rows), n_aggr, PROC_DIR / "20_phase1_prefetch_report.md")
    runner.save_raw({"phase": "1ab", "n_aggr": n_aggr, "quiescent": q, "rows": all_rows},
                    tag="20_phase1_prefetch")
    print(f"\nPhase 1(a)+(b) complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
