#!/usr/bin/env python3
"""
Phase 3.6 — Bandwidth-Matching Audit (H7)

Measures actual aggregate memory bandwidth for each aggressor condition
(A, B, C, D) using hardware performance counters (iMC uncore events):
  - UNC_M_CAS_COUNT.RD  (DRAM read CAS operations → bytes read from DRAM)
  - UNC_M_CAS_COUNT.WR  (DRAM write CAS operations)

Each CAS = 64 bytes (one cache line).

Conditions tested at Phase 2 core counts (A=2, B=3, C=3, D=2).
Also tests at 1 core (per-core BW calibration).

Outputs:
  results/processed/03_6_bw_audit.csv
  results/processed/03_6_bw_audit.md

H7 evaluation: if |BW_A − BW_B| / max(BW_A, BW_B) > 0.10, flag as FAIL.
"""

import sys
import csv
import time
import json
import subprocess
import statistics
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

NODE        = runner.NUMA_NODE
MEASURE_SEC = 5.0
WARMUP_SEC  = 3.0
N_TRIALS    = 5

PHASE2_CORES = {"A": 2, "B": 3, "C": 3, "D": 2}


def measure_dram_bw(duration_sec: float) -> Dict[str, float]:
    """
    Measure DRAM read+write bandwidth via iMC uncore PMU events.
    Returns {"read_gbps": float, "write_gbps": float, "total_gbps": float}.

    Tries iMC events first; falls back to LLC miss rate × cache line if unavailable.
    """
    # IMC events: sum across all memory controllers (typically 8 on SPR 2-socket,
    # 4 per socket; we use uncore_imc_0..3 for socket 0)
    imc_events = []
    for i in range(4):  # 4 iMC channels per socket on 8462Y+
        imc_events.append(f"uncore_imc_{i}/unc_m_cas_count.rd/")
        imc_events.append(f"uncore_imc_{i}/unc_m_cas_count.wr/")

    cmd = ["perf", "stat", "-a", "--no-big-num",
           "-e", ",".join(imc_events),
           "--", "sleep", f"{duration_sec:.1f}"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=duration_sec + 30)
    except subprocess.TimeoutExpired:
        log("  iMC perf stat timed out")
        return {}

    import re
    totals = {"rd": 0, "wr": 0}
    for line in result.stderr.splitlines():
        m = re.match(r'\s*([\d,]+)\s+uncore_imc_\d+/(unc_m_cas_count\.(rd|wr))/', line)
        if m:
            count = int(m.group(1).replace(",", ""))
            rw = m.group(3)
            totals[rw] = totals.get(rw, 0) + count

    if totals["rd"] == 0 and totals["wr"] == 0:
        # iMC events unavailable; fall back to reporting 0 and flagging
        log(f"  WARNING: iMC events returned 0. stderr: {result.stderr[:400]}")
        return {"read_gbps": 0.0, "write_gbps": 0.0, "total_gbps": 0.0,
                "fallback": True}

    BYTES_PER_CAS = 64
    read_bytes  = totals["rd"] * BYTES_PER_CAS
    write_bytes = totals["wr"] * BYTES_PER_CAS
    read_gbps   = read_bytes  / duration_sec / 1e9
    write_gbps  = write_bytes / duration_sec / 1e9
    return {
        "read_gbps":  round(read_gbps,  2),
        "write_gbps": round(write_gbps, 2),
        "total_gbps": round(read_gbps + write_gbps, 2),
        "cas_rd":     totals["rd"],
        "cas_wr":     totals["wr"],
        "fallback":   False,
    }


def run_bw_trial(condition: str, n_cores: int, trial: int) -> Dict:
    total_dur = WARMUP_SEC + MEASURE_SEC + 5.0
    aggressors = []
    for i in range(n_cores):
        a = AggressorProcess(condition, cpu=runner.AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    time.sleep(WARMUP_SEC + 2.0)

    bw = measure_dram_bw(MEASURE_SEC)

    for a in aggressors:
        a.stop()

    row = {
        "condition":    condition,
        "n_cores":      n_cores,
        "trial":        trial,
        "read_gbps":    bw.get("read_gbps", 0),
        "write_gbps":   bw.get("write_gbps", 0),
        "total_gbps":   bw.get("total_gbps", 0),
        "fallback":     bw.get("fallback", False),
        "cas_rd":       bw.get("cas_rd", 0),
        "cas_wr":       bw.get("cas_wr", 0),
    }
    log(f"  [{condition}] cores={n_cores} trial={trial}: "
        f"DRAM read={row['read_gbps']:.1f} write={row['write_gbps']:.1f} "
        f"total={row['total_gbps']:.1f} GB/s")
    return row


def eval_h7(summary: Dict[str, float]) -> str:
    lines = ["## H7 Evaluation (Bandwidth Matching)\n\n"]
    lines.append("**Pre-registered:** |BW_A − BW_B| / max(BW_A,BW_B) ≤ 0.10\n\n")
    lines.append("| Pair | BW_X (GB/s) | BW_Y (GB/s) | |ΔBW|/max | H7 |\n")
    lines.append("|------|-------------|-------------|-----------|----|\n")

    pairs = [("A", "B"), ("A", "C"), ("A", "D"), ("B", "C")]
    for x, y in pairs:
        if x not in summary or y not in summary:
            lines.append(f"| {x} vs {y} | N/A |\n")
            continue
        bx = summary[x]
        by = summary[y]
        if max(bx, by) == 0:
            lines.append(f"| {x} vs {y} | {bx:.1f} | {by:.1f} | N/A | N/A |\n")
            continue
        ratio = abs(bx - by) / max(bx, by)
        verdict = "PASS" if ratio <= 0.10 else "FAIL"
        lines.append(f"| {x} vs {y} | {bx:.1f} | {by:.1f} | {ratio:.2f} | {verdict} |\n")

    bw_a = summary.get("A", 0)
    bw_b = summary.get("B", 0)
    if bw_a > 0 and bw_b > 0:
        ratio_ab = abs(bw_a - bw_b) / max(bw_a, bw_b)
        if ratio_ab > 0.10:
            lines.append("\n**H7 FAILS for A vs B.** Phase 2 comparison is bandwidth-confounded.\n")
            lines.append(f"  BW_A = {bw_a:.1f} GB/s ({PHASE2_CORES['A']} cores), "
                         f"BW_B = {bw_b:.1f} GB/s ({PHASE2_CORES['B']} cores).\n")
            lines.append("  A bandwidth-matched re-run of Phase 2 is required.\n")
            lines.append("  Suggested: run both A and B at 2 cores, repeat at 3 cores.\n")
        else:
            lines.append("\n**H7 PASSES for A vs B.** Phase 2 bandwidth matching is acceptable.\n")

    return "".join(lines)


def write_report(all_rows: List[Dict], out_path: Path):
    by_cond: Dict[str, List[Dict]] = {}
    for r in all_rows:
        by_cond.setdefault(r["condition"], []).append(r)

    summary: Dict[str, float] = {}
    for cond, rows in by_cond.items():
        p2_rows = [r for r in rows if r["n_cores"] == PHASE2_CORES.get(cond, 999)]
        if p2_rows:
            summary[cond] = statistics.mean(r["total_gbps"] for r in p2_rows)

    with open(out_path, "w") as f:
        f.write("# Phase 3.6 — Bandwidth-Matching Audit\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## Measured DRAM Bandwidth at Phase 2 Core Counts\n\n")
        f.write("| Cond | Cores (P2) | DRAM Read (GB/s) | DRAM Write (GB/s) | "
                "Total (GB/s) | n trials |\n")
        f.write("|------|------------|------------------|-------------------|"
                "-------------|----------|\n")
        for cond in ["A", "B", "C", "D"]:
            rows = [r for r in all_rows
                    if r["condition"] == cond
                    and r["n_cores"] == PHASE2_CORES.get(cond, 0)]
            if not rows:
                f.write(f"| {cond} | {PHASE2_CORES.get(cond,'?')} | NO DATA |\n")
                continue
            rd_mean  = statistics.mean(r["read_gbps"]  for r in rows)
            wr_mean  = statistics.mean(r["write_gbps"] for r in rows)
            tot_mean = statistics.mean(r["total_gbps"] for r in rows)
            f.write(f"| {cond} | {PHASE2_CORES[cond]} | {rd_mean:.1f} | "
                    f"{wr_mean:.1f} | {tot_mean:.1f} | {len(rows)} |\n")

        f.write("\n## Per-Core Bandwidth Calibration (1 core each)\n\n")
        f.write("| Cond | DRAM Total (GB/s) @ 1 core |\n")
        f.write("|------|----------------------------|\n")
        for cond in ["A", "B", "C", "D"]:
            rows_1c = [r for r in all_rows
                       if r["condition"] == cond and r["n_cores"] == 1]
            if not rows_1c:
                f.write(f"| {cond} | NO DATA |\n")
                continue
            tot = statistics.mean(r["total_gbps"] for r in rows_1c)
            f.write(f"| {cond} | {tot:.1f} |\n")

        f.write("\n")
        f.write(eval_h7(summary))

        f.write("\n## Method Note\n\n")
        f.write("DRAM bandwidth measured via iMC uncore PMU events:\n")
        f.write("  `uncore_imc_N/unc_m_cas_count.rd/` and `unc_m_cas_count.wr/`\n")
        f.write("  summed across iMC channels 0–3 (socket 0).\n")
        f.write("  Each CAS operation = 64 bytes.\n")
        f.write("  If iMC events are unavailable, `fallback=True` and values are 0.\n")

    log(f"Wrote: {out_path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 3.6: Bandwidth-Matching Audit ===")

    # Verify iMC events are accessible
    test = subprocess.run(
        ["perf", "stat", "-e", "uncore_imc_0/unc_m_cas_count.rd/",
         "-a", "--", "sleep", "0.1"],
        capture_output=True, text=True, timeout=10
    )
    if test.returncode != 0:
        log("WARNING: iMC uncore events may not be accessible.")
        log(f"  {test.stderr[:300]}")
        log("  Will attempt anyway and report fallback if needed.")
    else:
        log("iMC PMU access: OK")

    all_rows: List[Dict] = []

    # Test at Phase 2 core counts AND at 1 core (per-core calibration)
    for condition in ["A", "B", "C", "D"]:
        p2_cores = PHASE2_CORES[condition]
        for n_cores in sorted(set([1, p2_cores])):
            log(f"\n[{condition}] n_cores={n_cores}:")
            for trial in range(N_TRIALS):
                row = run_bw_trial(condition, n_cores, trial)
                all_rows.append(row)
                runner.cooldown_sleep(1.0)
            runner.save_raw(
                [r for r in all_rows
                 if r["condition"] == condition and r["n_cores"] == n_cores],
                tag=f"03_6_bw_{condition}_{n_cores}c"
            )
            time.sleep(2.0)

    csv_path = PROC_DIR / "03_6_bw_audit.csv"
    fieldnames = ["condition", "n_cores", "trial",
                  "read_gbps", "write_gbps", "total_gbps",
                  "cas_rd", "cas_wr", "fallback"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)
    log(f"Wrote: {csv_path}")

    write_report(all_rows, PROC_DIR / "03_6_bw_audit.md")

    print(f"\nPhase 3.6 complete. Results: {csv_path}")


if __name__ == "__main__":
    main()
