#!/usr/bin/env python3
"""Phase 19.5 SF saturation control on Xeon Platinum 8592+."""

import csv
import json
import signal
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.5_sf_saturation.csv"
REPORT = ROOT / "results" / "processed" / "19.5_sf_saturation_report.md"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase_nocap"
TURNOVER = ROOT / "bench" / "aggressor" / "forced_turnover"

N_TRIALS = 30
MEASURE_SEC = 10
WARMUP_SEC = 8
NODE = 0
VICTIM_CPU = 0
AGGR_CPUS = list(range(1, 61))
REGION_BYTES = 4 * 1024 * 1024
WSS = 384 * 1024

CORE_EVENTS = "mem_load_retired.l2_hit,mem_load_retired.l3_hit,mem_load_retired.l3_miss,cpu-cycles"
CHA_EVENTS = ",".join([
    "uncore_cha_0/unc_cha_core_snp.evict_one/",
    "uncore_cha_0/unc_cha_core_snp.evict_gtone/",
    "uncore_cha_0/unc_cha_rxc_req_q1_retry.sf_victim/",
])


def parse_perf(stderr: str) -> dict[str, int]:
    out = {}
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) >= 3:
            raw = parts[0].strip()
            ev = parts[2].strip().lower().strip("/")
            try:
                out[ev] = out.get(ev, 0) + int(float(raw))
            except ValueError:
                pass
    return out


def start_aggressors():
    procs = []
    for idx, cpu in enumerate(AGGR_CPUS):
        cmd = ["numactl", "--membind=0", "--cpunodebind=0", "--",
               str(TURNOVER), "--cpu", str(cpu), "--node", "0",
               "--region-bytes", str(REGION_BYTES), "--seed", str(1000 + idx)]
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        procs.append(p)
    time.sleep(WARMUP_SEC)
    # Verify at least the processes are alive; stderr includes pf_disabled=1 but
    # is not drained until teardown.
    dead = [p.pid for p in procs if p.poll() is not None]
    if dead:
        raise RuntimeError(f"forced_turnover processes exited early: {dead[:5]}")
    return procs


def stop_aggressors(procs):
    pf_disabled = 0
    summaries = []
    for p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGTERM)
    for p in procs:
        try:
            out, err = p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            out, err = p.communicate()
        if "pf_disabled=1" in err:
            pf_disabled += 1
        if len(summaries) < 3:
            summaries.append(err.strip().splitlines()[0] if err.strip() else "")
    return pf_disabled, " | ".join(summaries)


def run_victim():
    cmd = ["numactl", "--membind=0", "--cpunodebind=0", "--",
           str(VICTIM), "--cpu", "0", "--node", "0", "--wss", str(WSS),
           "--trials", "1", "--run-sec", str(MEASURE_SEC)]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    core = subprocess.Popen(["perf", "stat", "-x,", "-C", "0", "-e", CORE_EVENTS,
                             "--", "sleep", str(MEASURE_SEC)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cha = subprocess.Popen(["perf", "stat", "-x,", "-a", "-e", CHA_EVENTS,
                            "--", "sleep", str(MEASURE_SEC)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, err = proc.communicate(timeout=MEASURE_SEC + 30)
    _, cerr = core.communicate(timeout=MEASURE_SEC + 20)
    _, herr = cha.communicate(timeout=MEASURE_SEC + 20)
    if proc.returncode != 0:
        raise RuntimeError(err)
    data = json.loads(out)[0]
    pmu = parse_perf(cerr)
    pmu.update(parse_perf(herr))
    return data, pmu


def run_cell(label: str, active: bool):
    procs = start_aggressors() if active else []
    pf_count = 0
    pf_summary = ""
    rows = []
    try:
        for t in range(N_TRIALS):
            data, pmu = run_victim()
            l2 = max(pmu.get("mem_load_retired.l2_hit", 0), 0)
            l3h = max(pmu.get("mem_load_retired.l3_hit", 0), 0)
            l3m = max(pmu.get("mem_load_retired.l3_miss", 0), 0)
            denom = l2 + l3h + l3m
            sf = max(pmu.get("uncore_cha_0/unc_cha_core_snp.evict_one", 0), 0) + max(pmu.get("uncore_cha_0/unc_cha_core_snp.evict_gtone", 0), 0)
            row = {
                "condition": label,
                "trial": t,
                "aggressor_cores": len(procs),
                "cycles_per_load": round(float(data["cycles_per_load"]), 3),
                "latency_ns": round(float(data["cycles_per_load"]) / float(data["tsc_hz"]) * 1e9, 2),
                "l2_hit": l2,
                "l3_hit": l3h,
                "l3_miss": l3m,
                "l2_hit_fraction": round(l2 / denom, 6) if denom else 0,
                "sf_evict_total": sf,
                "sf_evict_per_sec": round(sf / MEASURE_SEC, 1),
                "sf_victim_retry": max(pmu.get("uncore_cha_0/unc_cha_rxc_req_q1_retry.sf_victim", 0), 0),
                "pf_disabled_count": "",
                "pf_summary": "",
            }
            rows.append(row)
            print(label, t, row["cycles_per_load"], row["l2_hit_fraction"], row["sf_evict_per_sec"], flush=True)
            time.sleep(1)
    finally:
        if procs:
            pf_count, pf_summary = stop_aggressors(procs)
            for r in rows:
                r["pf_disabled_count"] = pf_count
                r["pf_summary"] = pf_summary
    return rows


def main():
    rows = run_cell("Q", False) + run_cell("R60", True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    q = [r for r in rows if r["condition"] == "Q"]
    r = [r for r in rows if r["condition"] == "R60"]
    qcy = [float(x["cycles_per_load"]) for x in q]
    rcy = [float(x["cycles_per_load"]) for x in r]
    qsf = statistics.mean(float(x["sf_evict_per_sec"]) for x in q)
    rsf = statistics.mean(float(x["sf_evict_per_sec"]) for x in r)
    tax = (statistics.mean(rcy) / statistics.mean(qcy) - 1) * 100
    REPORT.write_text(
        "# Phase 19.5 SF Saturation Report\n\n"
        f"Q cycles/load mean: {statistics.mean(qcy):.3f}\n\n"
        f"R60 cycles/load mean: {statistics.mean(rcy):.3f}\n\n"
        f"R60 tax: {tax:+.2f}%\n\n"
        f"Q SF evict/s mean: {qsf:.1f}\n\n"
        f"R60 SF evict/s mean: {rsf:.1f}\n\n"
        f"SF ratio R60/Q: {(rsf/qsf if qsf else 0):.1f}x\n\n"
        f"R60 pf_disabled_count: {r[0]['pf_disabled_count']}\n\n"
        f"Mean R60 L2 hit fraction: {statistics.mean(float(x['l2_hit_fraction']) for x in r):.6f}\n"
    )
    print(REPORT)


if __name__ == "__main__":
    main()
