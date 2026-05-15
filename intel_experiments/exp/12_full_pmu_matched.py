#!/usr/bin/env python3
"""
Phase 12 — Matched-Bandwidth Full-PMU Experiment (H12 + DR1/DR2)

Runs victim pointer-chase simultaneously with PMU collection at:
  - Victim core (CPU 0): MEM_LOAD_RETIRED.L3_MISS, L2_HIT
  - System-wide CHA (all 32 tiles): SF evictions, LLC victims, TOR DRD occupancy
  - iMC: CAS_COUNT.RD (bandwidth), RPQ_CYCLES (queue depth proxy)

Design: per-trial = 10 s measurement window. n=30 per (condition, core_count).
Aggressors: conditions A (WB+pf) and B (WB-nopf) at equal core counts 2, 3, 4.
Victim WSS: 32 MB (L3-scale regime where A > B effect was observed).

Output:
  results/processed/12_full_pmu_matched.csv   (one row per trial)
  results/processed/12_full_pmu_report.md
"""

import sys
import csv
import json
import re
import time
import subprocess
import statistics
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log, AggressorProcess

PROC_DIR     = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS     = 30
MEASURE_SEC  = 10.0      # victim + PMU window duration
WARMUP_SEC   = 5.0
COOLDOWN_SEC = 2.0
WSS_BYTES    = 32 * 1024 * 1024
VICTIM_CPU   = runner.VICTIM_CPU
AGGR_CPUS    = runner.AGGR_CPUS
NODE         = runner.NUMA_NODE
TEST_CORES   = [2, 3, 4]

# Per-trial victim: 10 one-second sub-windows so mean is taken over a uniform window
VICTIM_SUBTRIAL_SEC = 1.0
VICTIM_SUBTRIALS    = int(MEASURE_SEC / VICTIM_SUBTRIAL_SEC)  # 10

# Core events (CPU 0, victim's core)
CORE_EVENTS = ",".join([
    "mem_load_retired.l3_miss",
    "mem_load_retired.l2_hit",
])

# Uncore CHA events (summed over all 32 tiles via -a system-wide perf stat)
CHA_EVENTS = ",".join([
    "unc_cha_core_snp.evict_one",
    "unc_cha_core_snp.evict_gtone",
    "unc_cha_llc_victims.ia",
    "unc_cha_tor_occupancy.ia_miss_drd",
])

# iMC events
IMC_EVENTS = ",".join(
    [f"uncore_imc_{i}/cas_count_read/" for i in range(4)] +
    [f"uncore_imc_free_running_{i}/rpq_cycles/" for i in range(4)]
)


def run_perf_stat_background(events: str, cpu_spec: str,
                              duration_sec: float) -> subprocess.Popen:
    """Start a background perf stat process with --timeout. Returns the Popen object."""
    timeout_ms = int(duration_sec * 1000)
    if cpu_spec == "all":
        cpu_args = ["-a"]
    else:
        cpu_args = ["-C", cpu_spec]

    cmd = (["perf", "stat"] + cpu_args +
           ["--no-big-num", "--timeout", str(timeout_ms),
            "-e", events])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def parse_perf_stat(stderr_text: str) -> Dict[str, int]:
    """Parse perf stat stderr; returns {event_name: count}."""
    out = {}
    for line in stderr_text.splitlines():
        # "    12,345  event_name  # description"
        m = re.match(r'\s*([\d,]+)\s+([\w./]+)', line)
        if m:
            name  = m.group(1).replace(",", "")
            event = m.group(2).lower().rstrip("/")
            try:
                out[event] = int(name)
            except ValueError:
                pass
        # "<not supported>  event_name" or "<not counted>  event_name"
        m2 = re.match(r'\s*<(not supported|not counted)>\s+([\w./]+)', line)
        if m2:
            out[m2.group(2).lower()] = -1
    return out


def run_victim_with_pmu(measure_sec: float) -> Tuple[List[Dict], Dict[str, int], float]:
    """
    Start victim (VICTIM_SUBTRIALS × VICTIM_SUBTRIAL_SEC) and two concurrent
    perf stat processes (core and uncore events). Wait for victim to finish,
    then terminate the PMU collectors.

    Returns (victim_trials_list, merged_pmu_counts, actual_pmu_sec).
    """
    victim_cmd = (
        runner.pin_cmd(VICTIM_CPU, NODE) +
        [str(runner.VICTIM_BIN),
         "--cpu",    str(VICTIM_CPU),
         "--node",   str(NODE),
         "--wss",    str(WSS_BYTES),
         "--trials", str(VICTIM_SUBTRIALS),
         "--run-sec", f"{VICTIM_SUBTRIAL_SEC:.2f}"]
    )

    # PMU processes run for measure_sec seconds via --timeout
    # Victim runs VICTIM_SUBTRIALS × VICTIM_SUBTRIAL_SEC ≈ measure_sec
    pmu_dur = measure_sec

    t_start = time.monotonic()

    # Start victim first, then PMU (PMU --timeout is a wall-clock counter,
    # so small offset is acceptable; the victim runtime dominates)
    victim_proc = subprocess.Popen(
        victim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    core_proc   = run_perf_stat_background(CORE_EVENTS, str(VICTIM_CPU), pmu_dur)
    uncore_proc = run_perf_stat_background(
        CHA_EVENTS + "," + IMC_EVENTS, "all", pmu_dur
    )

    # Wait for all to finish (victim drives the measurement; PMU auto-expires)
    try:
        v_stdout, _ = victim_proc.communicate(timeout=measure_sec + 60)
    except subprocess.TimeoutExpired:
        victim_proc.kill()
        v_stdout, _ = victim_proc.communicate()
        log("  WARNING: victim timed out")

    t_end = time.monotonic()
    actual_pmu_sec = t_end - t_start

    # PMU processes have their own --timeout and should finish on their own
    # Wait for them to complete (they auto-exit after measure_sec ms)
    try:
        _, core_stderr   = core_proc.communicate(timeout=measure_sec + 10)
        _, uncore_stderr = uncore_proc.communicate(timeout=measure_sec + 10)
    except subprocess.TimeoutExpired:
        for p in (core_proc, uncore_proc):
            if p.poll() is None:
                p.kill()
        _, core_stderr   = core_proc.communicate()
        _, uncore_stderr = uncore_proc.communicate()

    # Parse victim output (JSON lines, one per sub-trial)
    trials = []
    for line in v_stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            trials.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    # Merge PMU counts
    pmu = {}
    pmu.update(parse_perf_stat(core_stderr))
    pmu.update(parse_perf_stat(uncore_stderr))

    return trials, pmu, actual_pmu_sec


def compute_pmu_rates(pmu: Dict[str, int], pmu_sec: float) -> Dict[str, float]:
    """Convert raw PMU counts to per-second rates."""
    def rate(key: str) -> float:
        v = pmu.get(key, 0)
        return round(v / pmu_sec, 1) if v > 0 else 0.0

    # SF evictions
    sf_one   = pmu.get("unc_cha_core_snp.evict_one",   0)
    sf_gtone = pmu.get("unc_cha_core_snp.evict_gtone",  0)
    sf_total = max(sf_one + sf_gtone, 0)

    # L2 hit rate at victim
    l3_miss = max(pmu.get("mem_load_retired.l3_miss", 0), 0)
    l2_hit  = max(pmu.get("mem_load_retired.l2_hit",  0), 0)
    total_loads = l3_miss + l2_hit
    l2_hit_rate = l2_hit / total_loads if total_loads > 0 else 0.0

    # LLC victims IA
    llc_vic = max(pmu.get("unc_cha_llc_victims.ia", 0), 0)

    # TOR DRD occupancy (cumulative; divide by freq to get avg depth, but we
    # just use count/sec as a relative proxy)
    tor_drd = max(pmu.get("unc_cha_tor_occupancy.ia_miss_drd", 0), 0)

    # iMC bandwidth
    cas_rd = sum(max(pmu.get(f"uncore_imc_{i}/cas_count_read", 0), 0) for i in range(4))
    imc_bw = round(cas_rd * 64 / pmu_sec / 1e9, 2)

    # RPQ cycles (read pending queue occupancy proxy)
    rpq = sum(max(pmu.get(f"uncore_imc_free_running_{i}/rpq_cycles", 0), 0) for i in range(4))

    return {
        "sf_evictions_per_sec": round(sf_total / pmu_sec, 1),
        "sf_evict_total":       sf_total,
        "llc_victims_per_sec":  round(llc_vic / pmu_sec, 1),
        "l3_misses_per_sec":    round(l3_miss / pmu_sec, 1),
        "l2_hits_per_sec":      round(l2_hit  / pmu_sec, 1),
        "l2_hit_rate":          round(l2_hit_rate, 4),
        "mc_queue_occ":         round(rpq / pmu_sec, 1),
        "tor_drd_occ":          round(tor_drd / pmu_sec, 1),
        "imc_bw_gbps":          imc_bw,
        "pmu_sec":              round(pmu_sec, 2),
    }


def run_cell(condition: str, n_cores: int, trials: int = N_TRIALS) -> List[Dict]:
    log(f"\n=== [{condition}] {n_cores} cores ===")

    total_dur = WARMUP_SEC + trials * (MEASURE_SEC + 3) + 30
    aggressors = []
    for i in range(n_cores):
        a = AggressorProcess(condition, cpu=AGGR_CPUS[i],
                             region_gb=1, duration_sec=total_dur, node=NODE)
        a.start()
        aggressors.append(a)

    log(f"  Warmup {WARMUP_SEC}s ...")
    time.sleep(WARMUP_SEC)

    if condition == "B":
        for i in range(n_cores):
            val = runner.read_msr(AGGR_CPUS[i], 0x1A4)
            if val is not None and (val & 0xF) != 0xF:
                log(f"  WARNING: cpu{AGGR_CPUS[i]} MSR 0x1A4=0x{val:x} (need 0xF)")

    rows = []
    for t in range(trials):
        v_trials, pmu, pmu_sec = run_victim_with_pmu(MEASURE_SEC)

        if not v_trials:
            log(f"  Trial {t}: no victim output, skipping")
            time.sleep(COOLDOWN_SEC)
            continue

        mean_cyc = statistics.mean(tr["cycles_per_load"] for tr in v_trials)
        rates = compute_pmu_rates(pmu, pmu_sec)
        agg_bw = sum(a.get_recent_bw_from_stderr() or 0.0 for a in aggressors)

        row = {
            "condition":              condition,
            "n_aggressor_cores":      n_cores,
            "trial":                  t,
            "victim_cycles_per_load": round(mean_cyc, 2),
            "victim_total_loads":     sum(tr.get("total_loads", 0) for tr in v_trials),
            "aggregate_bw_gbps":      rates["imc_bw_gbps"],
            "agg_reported_bw":        round(agg_bw, 2),
            "sf_evictions_per_sec":   rates["sf_evictions_per_sec"],
            "sf_evict_total":         rates["sf_evict_total"],
            "llc_victims_per_sec":    rates["llc_victims_per_sec"],
            "l3_misses_per_sec":      rates["l3_misses_per_sec"],
            "l2_hits_per_sec":        rates["l2_hits_per_sec"],
            "l2_hit_rate":            rates["l2_hit_rate"],
            "mc_queue_occ":           rates["mc_queue_occ"],
            "tor_drd_occ":            rates["tor_drd_occ"],
        }
        rows.append(row)

        if t % 5 == 0 or t == trials - 1:
            log(f"  t={t:2d}: cyc={mean_cyc:.1f} l3={rates['l3_misses_per_sec']:.0f}/s "
                f"sf={rates['sf_evictions_per_sec']:.0f}/s "
                f"llcv={rates['llc_victims_per_sec']:.0f}/s "
                f"bw={rates['imc_bw_gbps']:.1f}GB/s")

        time.sleep(COOLDOWN_SEC)

    for a in aggressors:
        a.stop()

    if rows:
        c = statistics.mean(r["victim_cycles_per_load"] for r in rows)
        log(f"  [{condition}@{n_cores}c] mean={c:.1f} n={len(rows)}")

    runner.save_raw({"condition": condition, "n_cores": n_cores, "rows": rows},
                    tag=f"12_fullpmu_{condition}_{n_cores}c")
    time.sleep(3.0)
    return rows


def run_quiescent(trials: int = N_TRIALS) -> List[Dict]:
    log("\n=== [Q] Quiescent ===")
    rows = []
    for t in range(trials):
        v_trials, pmu, pmu_sec = run_victim_with_pmu(MEASURE_SEC)
        if not v_trials:
            continue
        mean_cyc = statistics.mean(tr["cycles_per_load"] for tr in v_trials)
        rates = compute_pmu_rates(pmu, pmu_sec)
        rows.append({
            "condition": "Q", "n_aggressor_cores": 0, "trial": t,
            "victim_cycles_per_load": round(mean_cyc, 2),
            "victim_total_loads": sum(tr.get("total_loads", 0) for tr in v_trials),
            "aggregate_bw_gbps":   0.0,
            "agg_reported_bw":     0.0,
            "sf_evictions_per_sec":  rates["sf_evictions_per_sec"],
            "sf_evict_total":        rates["sf_evict_total"],
            "llc_victims_per_sec":   rates["llc_victims_per_sec"],
            "l3_misses_per_sec":     rates["l3_misses_per_sec"],
            "l2_hits_per_sec":       rates["l2_hits_per_sec"],
            "l2_hit_rate":           rates["l2_hit_rate"],
            "mc_queue_occ":          rates["mc_queue_occ"],
            "tor_drd_occ":           rates["tor_drd_occ"],
        })
        time.sleep(COOLDOWN_SEC)
    if rows:
        log(f"  Q mean={statistics.mean(r['victim_cycles_per_load'] for r in rows):.1f} n={len(rows)}")
    runner.save_raw({"condition": "Q", "rows": rows}, tag="12_fullpmu_Q")
    time.sleep(3.0)
    return rows


def write_csv(all_rows: List[Dict], path: Path):
    fieldnames = [
        "condition", "n_aggressor_cores", "trial", "victim_cycles_per_load",
        "victim_total_loads", "aggregate_bw_gbps", "agg_reported_bw",
        "sf_evictions_per_sec", "sf_evict_total", "llc_victims_per_sec",
        "l3_misses_per_sec", "l2_hits_per_sec", "l2_hit_rate",
        "mc_queue_occ", "tor_drd_occ",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    log(f"Wrote: {path}")


def write_report(q_rows: List[Dict],
                 results: Dict[Tuple[str, int], List[Dict]],
                 path: Path):
    q_cyc = statistics.mean(r["victim_cycles_per_load"] for r in q_rows) if q_rows else 0

    with open(path, "w") as f:
        f.write("# Phase 12 — Matched-Bandwidth Full-PMU Report\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")
        f.write(f"- WSS: {WSS_BYTES // 1024 // 1024} MB\n")
        f.write(f"- n_trials: {N_TRIALS} per cell\n")
        f.write(f"- Measure window: {MEASURE_SEC}s per trial\n\n")

        f.write("## Quiescent Baseline\n\n")
        if q_rows:
            q_l3 = statistics.mean(r["l3_misses_per_sec"] for r in q_rows)
            q_sf = statistics.mean(r["sf_evictions_per_sec"] for r in q_rows)
            f.write(f"| Q cycles | L3 miss/s | SF evict/s | n |\n")
            f.write(f"|----------|-----------|-----------|---|\n")
            f.write(f"| {q_cyc:.1f} | {q_l3:.0f} | {q_sf:.0f} | {len(q_rows)} |\n\n")

        f.write("## Per-Cell Summary\n\n")
        f.write("| Cond | Cores | n | Cyc/load | Tax | L3/s | SF evict/s | "
                "LLC vic/s | L2 hit% | BW GB/s |\n")
        f.write("|------|-------|---|----------|-----|------|-----------|"
                "----------|---------|--------|\n")

        for nc in TEST_CORES:
            for cond in ["A", "B"]:
                key = (cond, nc)
                if key not in results or not results[key]:
                    continue
                r = results[key]
                cyc  = statistics.mean(x["victim_cycles_per_load"] for x in r)
                l3   = statistics.mean(x["l3_misses_per_sec"] for x in r)
                sf   = statistics.mean(x["sf_evictions_per_sec"] for x in r)
                llcv = statistics.mean(x["llc_victims_per_sec"] for x in r)
                l2h  = statistics.mean(x["l2_hit_rate"] for x in r) * 100
                bw   = statistics.mean(x["aggregate_bw_gbps"] for x in r)
                tax  = (cyc - q_cyc) / q_cyc * 100 if q_cyc else 0
                f.write(f"| {cond} | {nc} | {len(r)} | {cyc:.1f} | +{tax:.0f}% | "
                        f"{l3:.0f} | {sf:.0f} | {llcv:.0f} | {l2h:.1f} | {bw:.1f} |\n")

        f.write("\n## H12 Data Completeness\n\n")
        all_r = [r for rows in results.values() for r in rows]
        checks = {
            "victim_cycles": any(r["victim_cycles_per_load"] > 0 for r in all_r),
            "sf_evictions":  any(r["sf_evictions_per_sec"]   > 0 for r in all_r),
            "llc_victims":   any(r["llc_victims_per_sec"]     > 0 for r in all_r),
            "l3_misses":     any(r["l3_misses_per_sec"]       > 0 for r in all_r),
            "imc_bw":        any(r["aggregate_bw_gbps"]       > 0 for r in all_r),
        }
        for k, v in checks.items():
            f.write(f"- {k}: {'PRESENT' if v else 'MISSING — see PMU_SUBSTITUTIONS.md'}\n")

        if all(checks.values()):
            f.write("\nAll H12 columns present. Run exp/13_partial_correlation.py.\n")
        else:
            f.write("\nMISSING COLUMNS: H12 partial correlation will be limited.\n")

    log(f"Wrote: {path}")


def main():
    runner.check_binaries()
    runner.check_env()

    log("=== Phase 12: Matched-Bandwidth Full-PMU Experiment ===")
    log(f"n={N_TRIALS}, measure={MEASURE_SEC}s, WSS={WSS_BYTES//1024//1024}MB, "
        f"cores={TEST_CORES}")

    all_rows = []
    results: Dict[Tuple[str, int], List[Dict]] = {}

    q_rows = run_quiescent(N_TRIALS)
    all_rows.extend(q_rows)

    for nc in TEST_CORES:
        for cond in ["A", "B"]:
            rows = run_cell(cond, nc, N_TRIALS)
            results[(cond, nc)] = rows
            all_rows.extend(rows)

    csv_path    = PROC_DIR / "12_full_pmu_matched.csv"
    report_path = PROC_DIR / "12_full_pmu_report.md"
    write_csv(all_rows, csv_path)
    write_report(q_rows, results, report_path)

    print(f"\nPhase 12 complete. {len(all_rows)} rows.")
    print(f"  CSV:    {csv_path}")
    print(f"  Report: {report_path}")
    print("  Next:   python3 exp/13_partial_correlation.py")


if __name__ == "__main__":
    main()
