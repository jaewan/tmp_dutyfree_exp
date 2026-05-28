#!/usr/bin/env python3
"""
Mitigation Trap Plan — Phase 2: Cache Allocation Technology (CAT) Sweep

Demonstrates the semantic mismatch of core-grained cache partitioning for
data-centric streaming. The victim core keeps ~80% of the L3 ways; the aggressor
ways are swept from the remaining ~20% down to 1. We record CXL bandwidth
and victim tax at each step. CAT cannot tell the aggressor's local heap data from
its streaming fills and does not partition the snoop filter, so the victim tax is
expected to persist even as the aggressor is squeezed to a single way.

REQUIRES root + mounted resctrl (sudo env/setup.sh).

Outputs:
  results/processed/21_phase2_cat.csv
  results/processed/21_phase2_cat_report.md
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
VIC_GROUP  = "mtrap_vic"
AGG_GROUP  = "mtrap_agg"


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

    n_aggr   = N_AGGR_FIXED
    total    = runner.resctrl_l3_total_ways()
    min_bits = runner.resctrl_l3_min_cbm_bits()
    cid      = runner.cpu_l3_cache_id(runner.VICTIM_CPU)   # victim's L3 domain
    victim_ways = max(1, round(0.8 * total))
    aggr_max    = max(1, total - victim_ways)
    aggr_cpus   = runner.AGGR_CPUS[:n_aggr]

    # Sweep aggressor ways from the spare ~20% down to min_cbm_bits.
    aggr_seq = list(range(aggr_max, max(min_bits, 1) - 1, -1))
    log(f"=== Phase 2: CAT sweep (L3 ways total={total}, min_cbm_bits={min_bits}, "
        f"cache_id={cid}) ===")
    log(f"victim_ways={victim_ways}, aggressor sweep={aggr_seq}, n_aggr={n_aggr}")

    q = mc.measure_quiescent(VICTIM_WSS, trials=10, run_sec=RUN_SEC, warmup_trials=3)
    log(f"quiescent baseline (no CAT, no aggressor): {q:.1f} cycles/load")

    all_rows = []
    try:
        # Baseline: aggressors running, NO CAT ("off").
        log("\n-- CAT off (aggressors unrestricted) --")
        trials, bw = mc.measure_point("A", n_aggr, victim_wss=VICTIM_WSS,
                                      trials=N_TRIALS, run_sec=RUN_SEC,
                                      warmup=WARMUP, readonly=True, warmup_trials=12)
        if trials:
            all_rows += mc.make_rows(trials, phase="2", knob="cat_aggr_ways",
                                     knob_value="off", condition="A",
                                     n_aggr_cores=n_aggr, quiescent=q, agg_bw=bw,
                                     readonly=True, notes="no_cat")
            log(f"  agg_bw={bw:.1f} GB/s tax={statistics.mean(r['tax_pct'] for r in all_rows):.1f}%")

        for aggr_ways in aggr_seq:
            vmask, amask = runner.cat_way_masks(total, victim_ways, aggr_ways)
            log(f"\n-- CAT victim={victim_ways}w (0x{vmask}) aggressor={aggr_ways}w (0x{amask}) --")
            vic = runner.resctrl_make_group(VIC_GROUP)
            agg = runner.resctrl_make_group(AGG_GROUP)
            try:
                runner.resctrl_write_schemata(vic, f"L3:{cid}={vmask}")
                runner.resctrl_write_schemata(agg, f"L3:{cid}={amask}")
                runner.resctrl_set_cpus(vic, [runner.VICTIM_CPU])
                runner.resctrl_set_cpus(agg, aggr_cpus)
                trials, bw = mc.measure_point("A", n_aggr, victim_wss=VICTIM_WSS,
                                              trials=N_TRIALS, run_sec=RUN_SEC,
                                              warmup=WARMUP, readonly=True,
                                              warmup_trials=12)
            finally:
                runner.resctrl_teardown([VIC_GROUP, AGG_GROUP])
            if not trials:
                log(f"  WARNING: no results at aggr_ways={aggr_ways}")
                continue
            rows = mc.make_rows(trials, phase="2", knob="cat_aggr_ways",
                                knob_value=aggr_ways, condition="A",
                                n_aggr_cores=n_aggr, quiescent=q, agg_bw=bw,
                                readonly=True, notes=f"vic{victim_ways}w_agg{aggr_ways}w")
            all_rows += rows
            log(f"  agg_bw={bw:.1f} GB/s tax={statistics.mean(r['tax_pct'] for r in rows):.1f}%")
    finally:
        runner.resctrl_teardown([VIC_GROUP, AGG_GROUP])

    csv_path = PROC_DIR / "21_phase2_cat.csv"
    mc.write_csv(all_rows, csv_path)

    import datetime
    with open(PROC_DIR / "21_phase2_cat_report.md", "w") as f:
        f.write("# Phase 2 Report — CAT Sweep\n")
        f.write(f"## Date: {datetime.datetime.now().isoformat()}\n")
        f.write(f"## L3 ways={total}, victim_ways={victim_ways}, n_aggr={n_aggr}, "
                f"quiescent={q:.1f} cyc/load\n\n")
        f.write("| aggr_ways | agg BW (GB/s) | tax % median | tax % mean±sd | n |\n")
        f.write("|----------:|--------------:|-------------:|--------------:|--:|\n")
        for key, s in sorted(mc.summarize(all_rows).items(), key=lambda kv: str(kv[0][0])):
            f.write(f"| {key[0]} | {s['bw_mean']:.2f} | {s['tax_median']:.2f} | "
                    f"{s['tax_mean']:.2f} ± {s['tax_std']:.2f} | {s['n']} |\n")
        f.write("\n**Expected:** victim tax persists as aggressor ways -> 1 "
                "(CAT cannot isolate streaming fills or the snoop filter).\n")
    log("Wrote report")
    runner.save_raw({"phase": "2", "total_ways": total, "victim_ways": victim_ways,
                     "quiescent": q, "rows": all_rows}, tag="21_phase2_cat")
    print(f"\nPhase 2 complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
