#!/usr/bin/env python3
"""
Mitigation Trap Plan — Phase 3: Memory Bandwidth Allocation (MBA) Sweep

Proves that bandwidth throttling only mitigates interference by destroying the
utility of the CXL tier. The aggressor cores are placed in an MBA group whose
throttle is swept from 90% down to ~10% (in bandwidth_gran steps). We record the
achieved CXL bandwidth and victim tax at each step to construct a Pareto
efficiency curve: the victim only recovers once the aggressor bandwidth has been
crushed toward WC levels.

REQUIRES root + mounted resctrl (sudo env/setup.sh).

Outputs:
  results/processed/23_phase3_mba.csv
  results/processed/23_phase3_mba_report.md
"""

import sys
import json
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log
import mitigation_common as mc

PROC_DIR = runner.RESULTS_PROC

VICTIM_WSS = 32 * 1024 * 1024
N_TRIALS   = 30
RUN_SEC    = 2.0
WARMUP     = 8.0
N_AGGR_FIXED = 8   # saturate the LLC reliably (see 20_phase1_prefetch.py)
AGG_GROUP  = "mtrap_mba"


def load_n_aggr(default: int = 4) -> int:
    try:
        raw = sorted(runner.RESULTS_RAW.glob("01_calibration_*.json"), reverse=True)
        if raw:
            return json.loads(raw[0].read_text())["core_counts"]["A"]["n_cores"]
    except Exception:
        pass
    return default


def main():
    runner.check_binaries()
    runner.require_resctrl()
    runner.check_env()

    n_aggr        = N_AGGR_FIXED
    min_bw, gran  = runner.resctrl_mb_info()
    cid           = runner.cpu_l3_cache_id(runner.VICTIM_CPU)   # aggressor's MB domain
    aggr_cpus     = runner.AGGR_CPUS[:n_aggr]

    # Sweep 90% down to min_bw in `gran` steps (clamped). 100% == "off" baseline.
    seq = []
    pct = 90
    while pct >= max(min_bw, 10):
        seq.append(pct)
        pct -= gran
    log(f"=== Phase 3: MBA sweep (min_bandwidth={min_bw}%, gran={gran}%, "
        f"cache_id={cid}) ===")
    log(f"throttle sweep={seq}, n_aggr={n_aggr}")

    q = mc.measure_quiescent(VICTIM_WSS, trials=10, run_sec=RUN_SEC, warmup_trials=3)
    log(f"quiescent baseline (no aggressor): {q:.1f} cycles/load")

    all_rows = []
    try:
        # Baseline: aggressors at full bandwidth (no MBA throttle).
        log("\n-- MBA off (100%) --")
        trials, bw = mc.measure_point("A", n_aggr, victim_wss=VICTIM_WSS,
                                      trials=N_TRIALS, run_sec=RUN_SEC,
                                      warmup=WARMUP, readonly=True, warmup_trials=12)
        if trials:
            all_rows += mc.make_rows(trials, phase="3", knob="mba_pct",
                                     knob_value=100, condition="A",
                                     n_aggr_cores=n_aggr, quiescent=q, agg_bw=bw,
                                     readonly=True, notes="no_throttle")
            log(f"  agg_bw={bw:.1f} GB/s tax={statistics.mean(r['tax_pct'] for r in all_rows):.1f}%")

        for pct in seq:
            log(f"\n-- MBA throttle = {pct}% --")
            agg = runner.resctrl_make_group(AGG_GROUP)
            try:
                runner.resctrl_write_schemata(agg, f"MB:{cid}={pct}")
                runner.resctrl_set_cpus(agg, aggr_cpus)
                trials, bw = mc.measure_point("A", n_aggr, victim_wss=VICTIM_WSS,
                                              trials=N_TRIALS, run_sec=RUN_SEC,
                                              warmup=WARMUP, readonly=True,
                                              warmup_trials=12)
            finally:
                runner.resctrl_teardown([AGG_GROUP])
            if not trials:
                log(f"  WARNING: no results at mba={pct}%")
                continue
            rows = mc.make_rows(trials, phase="3", knob="mba_pct", knob_value=pct,
                                condition="A", n_aggr_cores=n_aggr, quiescent=q,
                                agg_bw=bw, readonly=True, notes=f"mba{pct}")
            all_rows += rows
            log(f"  agg_bw={bw:.1f} GB/s tax={statistics.mean(r['tax_pct'] for r in rows):.1f}%")
    finally:
        runner.resctrl_teardown([AGG_GROUP])

    csv_path = PROC_DIR / "23_phase3_mba.csv"
    mc.write_csv(all_rows, csv_path)

    import datetime
    with open(PROC_DIR / "23_phase3_mba_report.md", "w") as f:
        f.write("# Phase 3 Report — MBA Sweep\n")
        f.write(f"## Date: {datetime.datetime.now().isoformat()}\n")
        f.write(f"## min_bandwidth={min_bw}%, gran={gran}%, n_aggr={n_aggr}, "
                f"quiescent={q:.1f} cyc/load\n\n")
        f.write("| mba_pct | agg BW (GB/s) | tax % median | tax % mean±sd | n |\n")
        f.write("|--------:|--------------:|-------------:|--------------:|--:|\n")
        for key, s in sorted(mc.summarize(all_rows).items(),
                             key=lambda kv: -float(kv[0][0])):
            f.write(f"| {key[0]} | {s['bw_mean']:.2f} | {s['tax_median']:.2f} | "
                    f"{s['tax_mean']:.2f} ± {s['tax_std']:.2f} | {s['n']} |\n")
        f.write("\n**Expected:** victim tax falls only once aggressor BW is "
                "throttled toward WC levels — the Pareto trap.\n")
    log("Wrote report")
    runner.save_raw({"phase": "3", "min_bw": min_bw, "gran": gran,
                     "quiescent": q, "rows": all_rows}, tag="23_phase3_mba")
    print(f"\nPhase 3 complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
