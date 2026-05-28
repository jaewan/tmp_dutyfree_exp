#!/usr/bin/env python3
"""
Mitigation Trap Plan — Phase 1(c): TLB Isolation

Runs the baseline WB stream (condition A, prefetch ON, read-only epoch) with the
aggressor region backed by 4KB, 2MB, then 1GB pages, and records the victim tax
at each. If the bottleneck were L2-TLB thrashing, larger pages (fewer TLB
entries) would relieve the victim; if it is data-array / LLC eviction, the tax
stays roughly constant across page sizes.

The 1GB point is skipped gracefully if 1GB hugepages are not reserved
(reserve with: sudo env NODE0_HP1G_TARGET=N env/setup.sh).

Outputs:
  results/processed/22_phase1c_tlb.csv
  results/processed/22_phase1c_tlb_report.md
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

VICTIM_WSS = 32 * 1024 * 1024   # 32 MB, LLC-resident
N_TRIALS   = 20
RUN_SEC    = 1.0
WARMUP     = 5.0
REGION_GB  = 1

PAGE_SIZES_KB = [4, 2048, 1048576]   # 4KB, 2MB, 1GB


def load_n_aggr(default: int = 4) -> int:
    try:
        raw = sorted(runner.RESULTS_RAW.glob("01_calibration_*.json"), reverse=True)
        if raw:
            return json.loads(raw[0].read_text())["core_counts"]["A"]["n_cores"]
    except Exception:
        pass
    return default


def free_1gb_pages() -> int:
    # Aggressor allocates its stream buffer on AGGR_MEM_NODE (the CXL node), so
    # check 1GB hugepage availability there.
    p = Path(f"/sys/devices/system/node/node{runner.AGGR_MEM_NODE}"
             "/hugepages/hugepages-1048576kB/free_hugepages")
    try:
        return int(p.read_text().strip())
    except Exception:
        return 0


def main():
    runner.check_binaries()
    runner.check_env()
    n_aggr = 8   # 8 cores saturate the LLC reliably (see 20_phase1_prefetch.py)
    log(f"=== Phase 1(c): TLB isolation page-size sweep ({n_aggr} aggressor cores) ===")

    q = mc.measure_quiescent(VICTIM_WSS, trials=10, run_sec=RUN_SEC, warmup_trials=3)
    log(f"quiescent baseline: {q:.1f} cycles/load")

    all_rows = []
    for pk in PAGE_SIZES_KB:
        if pk == 1048576:
            need = n_aggr * REGION_GB
            have = free_1gb_pages()
            if have < need:
                log(f"  SKIP 1GB pages: need {need} free, have {have} "
                    f"(reserve with NODE0_HP1G_TARGET=N env/setup.sh)")
                continue
        log(f"\n-- aggressor page_kb={pk} --")
        trials, agg_bw = mc.measure_point(
            "A", n_aggr, victim_wss=VICTIM_WSS, trials=N_TRIALS, run_sec=RUN_SEC,
            warmup=WARMUP, region_gb=REGION_GB, agg_page_kb=pk, readonly=True,
            warmup_trials=12)
        if not trials:
            log(f"  WARNING: no results at page_kb={pk}; skipping")
            continue
        rows = mc.make_rows(trials, phase="1c", knob="page_kb", knob_value=pk,
                            condition="A", n_aggr_cores=n_aggr, quiescent=q,
                            agg_bw=agg_bw, page_kb=pk, readonly=True)
        all_rows.extend(rows)
        log(f"  agg_bw={agg_bw:.1f} GB/s, victim tax mean="
            f"{statistics.mean(r['tax_pct'] for r in rows):.1f}%")

    csv_path = PROC_DIR / "22_phase1c_tlb.csv"
    mc.write_csv(all_rows, csv_path)

    import datetime
    with open(PROC_DIR / "22_phase1c_tlb_report.md", "w") as f:
        f.write("# Phase 1(c) Report — TLB Isolation\n")
        f.write(f"## Date: {datetime.datetime.now().isoformat()}\n\n")
        f.write("| page_kb | agg BW (GB/s) | victim tax % (mean±sd) | n |\n")
        f.write("|--------:|--------------:|-----------------------:|--:|\n")
        for key, s in sorted(mc.summarize(all_rows).items()):
            f.write(f"| {key[0]} | {s['bw_mean']:.2f} | "
                    f"{s['tax_mean']:.2f} ± {s['tax_std']:.2f} | {s['n']} |\n")
        f.write("\n**Expected:** victim tax roughly flat across page sizes → "
                "bottleneck is data-array/LLC eviction, not L2-TLB thrashing.\n")
    log(f"Wrote report")
    runner.save_raw({"phase": "1c", "n_aggr": n_aggr, "quiescent": q, "rows": all_rows},
                    tag="22_phase1c_tlb")
    print(f"\nPhase 1(c) complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
