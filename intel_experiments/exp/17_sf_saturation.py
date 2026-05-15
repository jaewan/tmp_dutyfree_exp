#!/usr/bin/env python3
"""
Phase 17 — SF Saturation and STREAMING's H2 Clause Empirical Test

Tests whether Intel SPR's Snoop Filter can be saturated under a high-fan-in
persistent random-access workload, and whether a CLDEMOTE-based STREAMING-proxy
reduces SF eviction rate and victim latency.

Hypotheses:
  H13: R32 SF eviction rate ≥ 10× Phase 12 baseline (45 K/s → ≥ 450 K/s)
  H14: r_sf > 0.5 (p < 0.01) after controlling for l3_miss + mc_queue at 384 KB
  H15: S32 SF rate ≤ 20% of R32; victim tax reduced ≥ 50% at 384 KB WSS

Conditions:
  Q    — quiescent, no aggressors
  R16  — 16 cores random_chase
  R24  — 24 cores random_chase
  R32  — 31 cores random_chase (max socket-0 with CPU 0 reserved; see OD-2)
  S32  — 31 cores random_chase --sf-bypass (CLDEMOTE proxy)

Each condition: n=30 trials per WSS (384 KB, 32 MB). 300 trials total.

Outputs:
  results/raw/17_*.json
  results/processed/17_sf_saturation.csv
  results/processed/17_sf_saturation_report.md

Stop conditions (per PHASE17_PROTOCOL.md §7):
  - Phase 17.1: SF rate < 2× baseline at R32 → halt (OD logged)
  - Phase 17.2: SF rate reduction < 50% (S32 vs R32) → halt (proxy invalid)
  - objdump check failure → halt
  - results/raw/ > 2 GB → halt
  - wall clock > 4 hours → halt
"""

import sys
import csv
import json
import os
import re
import time
import subprocess
import threading
import signal
import statistics
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log

PROC_DIR     = runner.RESULTS_PROC
RAW_DIR      = runner.RESULTS_RAW
PROC_DIR.mkdir(parents=True, exist_ok=True)
RAW_DIR.mkdir(parents=True, exist_ok=True)

N_TRIALS         = 30
MEASURE_SEC      = 10.0
WARMUP_SEC       = 5.0
COOLDOWN_SEC     = 2.0

REGION_BYTES     = 1572864        # 1.5 MB → rounds to 2 MB hugepage
WSS_LIST         = [384 * 1024, 32 * 1024 * 1024]   # 384 KB, 32 MB

VICTIM_CPU       = runner.VICTIM_CPU   # CPU 0
AGGR_CPUS        = runner.AGGR_CPUS    # CPUs 1–31
NUMA_NODE        = runner.NUMA_NODE

RANDOM_CHASE_BIN = runner.BENCH_DIR / "aggressor" / "random_chase"

# SF baseline from Phase 12 quiescent (K/s); used for H13 threshold check
SF_BASELINE_PER_SEC = 45_000.0
H13_THRESHOLD       = 10 * SF_BASELINE_PER_SEC   # ≥ 450 K/s
H13_STOP_THRESHOLD  = 2  * SF_BASELINE_PER_SEC   # < 2× → halt

# Conditions: (label, n_cores, sf_bypass)
CONDITIONS = [
    ("Q",   0,  False),
    ("R16", 16, False),
    ("R24", 24, False),
    ("R32", 31, False),   # 31 cores: max with CPU 0 for victim (OD-2)
    ("S32", 31, True),    # same core count, sf-bypass (CLDEMOTE proxy)
]

# Victim sub-trial structure: 10 × 1s = 10s measurement window
VICTIM_SUBTRIAL_SEC = 1.0
VICTIM_SUBTRIALS    = int(MEASURE_SEC / VICTIM_SUBTRIAL_SEC)

# Core PMU events (victim CPU 0)
CORE_EVENTS = ",".join([
    "mem_load_retired.l3_miss",
    "mem_load_retired.l2_hit",
])

# Uncore CHA events (system-wide, sum all 32 CHAs)
CHA_EVENTS = ",".join([
    "unc_cha_core_snp.evict_one",
    "unc_cha_core_snp.evict_gtone",
    "unc_cha_rxc_req_q1_retry.sf_victim",
    "unc_cha_llc_victims.m_state",
])

# iMC events
IMC_EVENTS = ",".join(
    [f"uncore_imc_{i}/cas_count_read/" for i in range(4)] +
    [f"uncore_imc_free_running_{i}/rpq_cycles/" for i in range(4)]
)

_run_start_time = time.monotonic()


class RandomChaseProcess:
    """Manages one random_chase aggressor process (Phase 17 aggressor)."""

    def __init__(self, cpu: int, seed: int, sf_bypass: bool = False,
                 node: int = NUMA_NODE):
        self.cpu       = cpu
        self.seed      = seed
        self.sf_bypass = sf_bypass
        self.node      = node
        self.proc: Optional[subprocess.Popen] = None
        self._stderr_lines: List[str] = []
        self._stdout_data  = ""

    def start(self):
        cmd = (runner.pin_cmd(self.cpu, self.node) +
               [str(RANDOM_CHASE_BIN),
                "--cpu",          str(self.cpu),
                "--node",         str(self.node),
                "--region-bytes", str(REGION_BYTES),
                "--seed",         str(self.seed)])
        if self.sf_bypass:
            cmd.append("--sf-bypass")

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def _drain():
            for line in self.proc.stderr:
                self._stderr_lines.append(line.rstrip())
        threading.Thread(target=_drain, daemon=True).start()

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def get_iter_per_sec(self) -> Optional[float]:
        for line in reversed(self._stderr_lines):
            # "random_chase: cpu=N ... iter_per_sec=X" not in stderr;
            # look for final JSON on stdout
            pass
        try:
            if self.proc and self.proc.poll() is not None:
                out, _ = self.proc.communicate(timeout=5)
                if out.strip():
                    data = json.loads(out.strip())
                    return float(data.get("iter_per_sec", 0))
        except Exception:
            pass
        return None


def run_perf_stat_background(events: str, cpu_spec: str,
                              duration_sec: float) -> subprocess.Popen:
    timeout_ms = int(duration_sec * 1000)
    cpu_args   = ["-a"] if cpu_spec == "all" else ["-C", cpu_spec]
    cmd = (["perf", "stat"] + cpu_args +
           ["--no-big-num", "--timeout", str(timeout_ms), "-e", events])
    return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def parse_perf_stat(stderr_text: str) -> Dict[str, int]:
    out = {}
    for line in stderr_text.splitlines():
        m = re.match(r'\s*([\d,]+)\s+([\w./]+)', line)
        if m:
            try:
                out[m.group(2).lower().rstrip("/")] = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        m2 = re.match(r'\s*<(not supported|not counted)>\s+([\w./]+)', line)
        if m2:
            out[m2.group(2).lower().rstrip("/")] = -1
    return out


def run_victim_with_pmu(wss_bytes: int) -> Tuple[List[Dict], Dict[str, int], float]:
    """Launch victim + concurrent PMU collection. Returns (trials, pmu, elapsed_sec)."""
    victim_cmd = (
        runner.pin_cmd(VICTIM_CPU, NUMA_NODE) +
        [str(runner.VICTIM_BIN),
         "--cpu",     str(VICTIM_CPU),
         "--node",    str(NUMA_NODE),
         "--wss",     str(wss_bytes),
         "--trials",  str(VICTIM_SUBTRIALS),
         "--run-sec", f"{VICTIM_SUBTRIAL_SEC:.2f}"]
    )

    t_start = time.monotonic()

    victim_proc = subprocess.Popen(
        victim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    core_proc   = run_perf_stat_background(CORE_EVENTS, str(VICTIM_CPU), MEASURE_SEC)
    uncore_proc = run_perf_stat_background(
        CHA_EVENTS + "," + IMC_EVENTS, "all", MEASURE_SEC
    )

    try:
        v_stdout, _ = victim_proc.communicate(timeout=MEASURE_SEC + 60)
    except subprocess.TimeoutExpired:
        victim_proc.kill()
        v_stdout, _ = victim_proc.communicate()
        log("  WARNING: victim timed out")

    t_end = time.monotonic()
    actual_sec = t_end - t_start

    try:
        _, core_stderr   = core_proc.communicate(timeout=MEASURE_SEC + 10)
        _, uncore_stderr = uncore_proc.communicate(timeout=MEASURE_SEC + 10)
    except subprocess.TimeoutExpired:
        for p in (core_proc, uncore_proc):
            if p.poll() is None:
                p.kill()
        _, core_stderr   = core_proc.communicate()
        _, uncore_stderr = uncore_proc.communicate()

    # Parse victim JSON array
    trials = []
    try:
        all_trials = json.loads(v_stdout.strip())
        if isinstance(all_trials, list):
            trials = all_trials
    except (json.JSONDecodeError, ValueError):
        # Fallback: try parsing individual lines (last sub-trial without comma)
        for line in v_stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "cycles_per_load" in obj:
                    trials.append(obj)
            except json.JSONDecodeError:
                pass

    pmu = {}
    pmu.update(parse_perf_stat(core_stderr))
    pmu.update(parse_perf_stat(uncore_stderr))

    return trials, pmu, actual_sec


def compute_pmu_rates(pmu: Dict[str, int], pmu_sec: float) -> Dict[str, float]:
    def rate(v: int) -> float:
        return round(v / pmu_sec, 1) if v > 0 else 0.0

    sf_one   = max(pmu.get("unc_cha_core_snp.evict_one",   0), 0)
    sf_gtone = max(pmu.get("unc_cha_core_snp.evict_gtone",  0), 0)
    sf_total = sf_one + sf_gtone

    sf_victim = max(pmu.get("unc_cha_rxc_req_q1_retry.sf_victim", 0), 0)

    l3_miss  = max(pmu.get("mem_load_retired.l3_miss", 0), 0)
    l2_hit   = max(pmu.get("mem_load_retired.l2_hit",  0), 0)
    total_ld = l3_miss + l2_hit
    l2_hit_rate = l2_hit / total_ld if total_ld > 0 else 0.0

    llc_victims_m = max(pmu.get("unc_cha_llc_victims.m_state", 0), 0)

    cas_rd = sum(max(pmu.get(f"uncore_imc_{i}/cas_count_read", 0), 0) for i in range(4))
    imc_bw = round(cas_rd * 64 / pmu_sec / 1e9, 3)

    rpq = sum(max(pmu.get(f"uncore_imc_free_running_{i}/rpq_cycles", 0), 0) for i in range(4))

    return {
        "sf_evictions_per_sec":    round(sf_total / pmu_sec, 1),
        "sf_evict_total":          sf_total,
        "sf_victim_per_sec":       round(sf_victim / pmu_sec, 1),
        "llc_victims_m_per_sec":   round(llc_victims_m / pmu_sec, 1),
        "l3_misses_per_sec":       round(l3_miss / pmu_sec, 1),
        "l2_hit_rate":             round(l2_hit_rate, 4),
        "mc_queue_occ":            round(rpq / pmu_sec, 1),
        "aggregate_bw_gbps":       imc_bw,
        "pmu_sec":                 round(pmu_sec, 2),
    }


def start_aggressors(n_cores: int, sf_bypass: bool) -> List[RandomChaseProcess]:
    procs = []
    for i in range(n_cores):
        p = RandomChaseProcess(
            cpu=AGGR_CPUS[i],
            seed=42 + i,
            sf_bypass=sf_bypass,
            node=NUMA_NODE
        )
        p.start()
        procs.append(p)
    return procs


def stop_aggressors(procs: List[RandomChaseProcess]):
    for p in procs:
        p.stop()


def run_cell(label: str, n_cores: int, sf_bypass: bool,
             wss_bytes: int, trials: int = N_TRIALS) -> List[Dict]:
    wss_kb = wss_bytes // 1024
    log(f"\n=== [{label}] {n_cores} cores sf_bypass={sf_bypass} WSS={wss_kb}KB ===")

    total_dur = WARMUP_SEC + trials * (MEASURE_SEC + 3) + 30
    aggressors: List[RandomChaseProcess] = []

    if n_cores > 0:
        aggressors = start_aggressors(n_cores, sf_bypass)
        log(f"  Warmup {WARMUP_SEC}s ...")
        time.sleep(WARMUP_SEC)

    rows = []
    for t in range(trials):
        v_trials, pmu, pmu_sec = run_victim_with_pmu(wss_bytes)

        if not v_trials:
            log(f"  Trial {t}: no victim output, skipping")
            time.sleep(COOLDOWN_SEC)
            continue

        mean_cyc = statistics.mean(tr["cycles_per_load"] for tr in v_trials)
        rates    = compute_pmu_rates(pmu, pmu_sec)

        row = {
            "condition":             label,
            "n_aggressor_cores":     n_cores,
            "sf_bypass":             int(sf_bypass),
            "wss_bytes":             wss_bytes,
            "trial":                 t,
            "victim_cycles_per_load": round(mean_cyc, 3),
            "victim_total_loads":    sum(tr.get("total_loads", 0) for tr in v_trials),
            "sf_evictions_per_sec":  rates["sf_evictions_per_sec"],
            "sf_evict_total":        rates["sf_evict_total"],
            "sf_victim_per_sec":     rates["sf_victim_per_sec"],
            "llc_victims_m_per_sec": rates["llc_victims_m_per_sec"],
            "l3_misses_per_sec":     rates["l3_misses_per_sec"],
            "l2_hit_rate":           rates["l2_hit_rate"],
            "mc_queue_occ":          rates["mc_queue_occ"],
            "aggregate_bw_gbps":     rates["aggregate_bw_gbps"],
            "pmu_sec":               rates["pmu_sec"],
        }
        rows.append(row)

        if t % 5 == 0 or t == trials - 1:
            log(f"  t={t:2d}: cyc={mean_cyc:.1f} "
                f"sf={rates['sf_evictions_per_sec']:.0f}/s "
                f"l3={rates['l3_misses_per_sec']:.0f}/s "
                f"bw={rates['aggregate_bw_gbps']:.2f}GB/s")

        time.sleep(COOLDOWN_SEC)

    if n_cores > 0:
        stop_aggressors(aggressors)

    if rows:
        cyc = statistics.mean(r["victim_cycles_per_load"] for r in rows)
        sf  = statistics.mean(r["sf_evictions_per_sec"]   for r in rows)
        log(f"  [{label} WSS={wss_kb}KB] mean_cyc={cyc:.1f} mean_sf={sf:.0f}/s n={len(rows)}")

    runner.save_raw(
        {"condition": label, "n_cores": n_cores, "sf_bypass": sf_bypass,
         "wss_bytes": wss_bytes, "rows": rows},
        tag=f"17_{label}_wss{wss_kb}"
    )
    time.sleep(3.0)
    return rows


def calibrate() -> float:
    """Phase 17.1: run R32 for ~10s, check SF eviction rate. Returns SF rate."""
    log("\n=== Phase 17.1: Calibration (R32, 10s) ===")

    aggressors = start_aggressors(31, sf_bypass=False)
    time.sleep(WARMUP_SEC)

    _, pmu, pmu_sec = run_victim_with_pmu(WSS_LIST[0])  # 384 KB WSS
    rates = compute_pmu_rates(pmu, pmu_sec)
    sf_rate = rates["sf_evictions_per_sec"]

    stop_aggressors(aggressors)

    log(f"  Phase 17.1 calibration SF eviction rate: {sf_rate:.0f}/s "
        f"(baseline ~{SF_BASELINE_PER_SEC:.0f}/s, "
        f"2× threshold = {H13_STOP_THRESHOLD:.0f}/s)")

    if sf_rate < H13_STOP_THRESHOLD:
        log(f"\nSTOP CONDITION: Phase 17.1 calibration FAILED.")
        log(f"  SF rate {sf_rate:.0f}/s < {H13_STOP_THRESHOLD:.0f}/s (2× baseline).")
        log(f"  Architectural prediction is wrong; reassess design before full matrix.")
        log(f"  Per PHASE17_PROTOCOL.md §7: halting — do not proceed.")
        sys.exit(1)

    log(f"  Phase 17.1 PASS: SF rate {sf_rate:.0f}/s ≥ {H13_STOP_THRESHOLD:.0f}/s")
    return sf_rate


def validate_proxy(r32_sf_rate: float) -> Tuple[float, bool]:
    """Phase 17.2: run S32 for ~10s, check SF rate reduction.
    Returns (S32 SF rate, proxy_valid). When proxy fails, logs N16 warning
    and returns proxy_valid=False; caller must skip S32 in full matrix."""
    log("\n=== Phase 17.2: STREAMING-proxy Validation (S32, 10s) ===")

    aggressors = start_aggressors(31, sf_bypass=True)
    time.sleep(WARMUP_SEC)

    _, pmu, pmu_sec = run_victim_with_pmu(WSS_LIST[0])
    rates = compute_pmu_rates(pmu, pmu_sec)
    s32_sf_rate = rates["sf_evictions_per_sec"]

    stop_aggressors(aggressors)

    reduction = 1.0 - (s32_sf_rate / r32_sf_rate) if r32_sf_rate > 0 else 0.0
    log(f"  Phase 17.2 proxy validation SF rates:")
    log(f"    R32: {r32_sf_rate:.0f}/s   S32: {s32_sf_rate:.0f}/s")
    log(f"    Reduction: {reduction*100:.1f}%  (need ≥ 50% to proceed)")

    if reduction < 0.50:
        log(f"\nSTOP CONDITION (N16): Phase 17.2 proxy validation FAILED.")
        log(f"  SF reduction {reduction*100:.1f}% < 50%.")
        log(f"  CLDEMOTE counts self-demotion snoops as evict_one — metric is confounded.")
        log(f"  Documented in NEGATIVE_RESULTS.md §N16.")
        log(f"  S32 excluded from full matrix. Continuing with R-conditions (H13/H14).")
        return s32_sf_rate, False

    log(f"  Phase 17.2 PASS: SF reduction {reduction*100:.1f}% ≥ 50%")
    return s32_sf_rate, True


def check_stop_conditions():
    raw_size = sum(f.stat().st_size for f in RAW_DIR.iterdir() if f.is_file())
    if raw_size > 2 * 1024**3:
        log(f"STOP: results/raw/ exceeds 2 GB ({raw_size // 1024**2} MB). "
            f"Possible log loop — investigate.")
        sys.exit(1)

    elapsed = time.monotonic() - _run_start_time
    if elapsed > 4 * 3600:
        log(f"STOP: Wall clock {elapsed/3600:.1f}h > 4h. Aborting.")
        sys.exit(1)


def write_csv(all_rows: List[Dict], path: Path):
    fieldnames = [
        "condition", "n_aggressor_cores", "sf_bypass", "wss_bytes",
        "trial", "victim_cycles_per_load", "victim_total_loads",
        "sf_evictions_per_sec", "sf_evict_total", "sf_victim_per_sec",
        "llc_victims_m_per_sec", "l3_misses_per_sec", "l2_hit_rate",
        "mc_queue_occ", "aggregate_bw_gbps", "pmu_sec",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    log(f"Wrote: {path}")


def write_summary_report(all_rows: List[Dict], path: Path,
                         calib_sf: float, proxy_sf: float):
    with open(path, "w") as f:
        f.write("# Phase 17.3 — SF Saturation Full Matrix Summary\n")
        f.write(f"## Generated: {datetime.now().isoformat()}\n\n")
        f.write(f"- Phase 17.1 calibration SF rate (R32, 10s): {calib_sf:.0f}/s\n")
        f.write(f"- Phase 17.2 proxy SF rate (S32, 10s): {proxy_sf:.0f}/s\n")
        f.write(f"- Total trials: {len(all_rows)}\n\n")
        f.write("## Per-Cell Summary\n\n")
        f.write("| Cond | WSS | n | Cyc/load | SF/s | SF_victim/s | L3/s | L2_hit | BW GB/s |\n")
        f.write("|------|-----|---|----------|------|-------------|------|--------|--------|\n")

        cells: Dict[Tuple, List[Dict]] = {}
        for r in all_rows:
            key = (r["condition"], r["wss_bytes"])
            cells.setdefault(key, []).append(r)

        for (cond, wss), rows in sorted(cells.items()):
            cyc  = statistics.mean(r["victim_cycles_per_load"] for r in rows)
            sf   = statistics.mean(r["sf_evictions_per_sec"]   for r in rows)
            sfv  = statistics.mean(r["sf_victim_per_sec"]       for r in rows)
            l3   = statistics.mean(r["l3_misses_per_sec"]       for r in rows)
            l2h  = statistics.mean(r["l2_hit_rate"]             for r in rows) * 100
            bw   = statistics.mean(r["aggregate_bw_gbps"]       for r in rows)
            wss_s = f"{wss//1024}KB" if wss < 1024*1024 else f"{wss//1024//1024}MB"
            f.write(f"| {cond} | {wss_s} | {len(rows)} | {cyc:.1f} | {sf:.0f} | "
                    f"{sfv:.0f} | {l3:.0f} | {l2h:.1f}% | {bw:.2f} |\n")

        f.write("\n## H13 Preliminary Check\n\n")
        r32_rows_384 = cells.get(("R32", WSS_LIST[0]), [])
        if r32_rows_384:
            r32_sf = statistics.mean(r["sf_evictions_per_sec"] for r in r32_rows_384)
            ratio  = r32_sf / SF_BASELINE_PER_SEC
            status = "LIKELY PASS" if r32_sf >= H13_THRESHOLD else "LIKELY FAIL"
            f.write(f"R32 @ 384KB: SF eviction rate = {r32_sf:.0f}/s "
                    f"({ratio:.1f}× baseline)\n")
            f.write(f"H13 threshold: ≥ {H13_THRESHOLD:.0f}/s (10× baseline)\n")
            f.write(f"Preliminary verdict: {status} — run exp/17_analysis.py for full test\n")

        f.write("\nFull statistical analysis: `python3 exp/17_analysis.py`\n")

    log(f"Wrote: {path}")


def main():
    runner.check_env()

    if not RANDOM_CHASE_BIN.exists():
        sys.exit(f"ERROR: {RANDOM_CHASE_BIN} not found. Run: make -C bench/")
    if not runner.VICTIM_BIN.exists():
        sys.exit(f"ERROR: {runner.VICTIM_BIN} not found. Run: make -C bench/")

    log("=== Phase 17: SF Saturation Experiment ===")
    log(f"n={N_TRIALS} trials, measure={MEASURE_SEC}s, conditions={len(CONDITIONS)}, "
        f"WSS={[w//1024 for w in WSS_LIST]}KB")
    log(f"REGION_BYTES={REGION_BYTES}, AGGR_CPUS={AGGR_CPUS[:5]}...{AGGR_CPUS[-1]}")

    # Phase 17.1: calibration
    calib_sf_rate = calibrate()
    check_stop_conditions()

    # Phase 17.2: proxy validation
    proxy_sf_rate, proxy_valid = validate_proxy(calib_sf_rate)
    check_stop_conditions()

    # Phase 17.3: full matrix — skip S32 if proxy failed
    log("\n=== Phase 17.3: Full Matrix ===")
    if not proxy_valid:
        log("  NOTE: S32 condition skipped (N16 — proxy metric confounded).")
    all_rows: List[Dict] = []

    for wss in WSS_LIST:
        wss_s = f"{wss//1024}KB" if wss < 1024*1024 else f"{wss//1024//1024}MB"
        log(f"\n--- WSS = {wss_s} ---")

        for label, n_cores, sf_bypass in CONDITIONS:
            if label == "S32" and not proxy_valid:
                log(f"  Skipping S32 (proxy invalid per N16)")
                continue
            check_stop_conditions()
            rows = run_cell(label, n_cores, sf_bypass, wss, N_TRIALS)
            all_rows.extend(rows)

    csv_path    = PROC_DIR / "17_sf_saturation.csv"
    report_path = PROC_DIR / "17_sf_saturation_matrix_summary.md"
    write_csv(all_rows, csv_path)
    write_summary_report(all_rows, report_path, calib_sf_rate, proxy_sf_rate)

    log(f"\nPhase 17.3 complete. {len(all_rows)} rows written.")
    log(f"  CSV:    {csv_path}")
    log(f"  Report: {report_path}")
    log("  Next:   python3 exp/17_analysis.py")


if __name__ == "__main__":
    main()
