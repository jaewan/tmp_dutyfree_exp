#!/usr/bin/env python3
"""Phase 19.0 PMU attribution for CXL latency validation."""

import csv
import json
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.0_pmu_attribution.csv"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase"

EVENTS = [
    "mem_load_retired.l1_hit",
    "mem_load_retired.l2_hit",
    "mem_load_retired.l3_hit",
    "mem_load_retired.l3_miss",
    "longest_lat_cache.miss",
]


def descendants(pid: int) -> list[int]:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    kids = [int(x) for x in out.stdout.split()] if out.stdout.strip() else []
    all_pids = kids[:]
    for kid in kids:
        all_pids.extend(descendants(kid))
    return all_pids


def find_pointer_chase(root_pid: int) -> int | None:
    for pid in descendants(root_pid):
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except FileNotFoundError:
            continue
        if comm == "pointer_chase":
            return pid
    return None


def read_numa(pid: int | None) -> tuple[str, int, int]:
    if pid is None:
        return "NO_POINTER_CHASE_PID", 0, 0
    try:
        text = Path(f"/proc/{pid}/numa_maps").read_text()
    except FileNotFoundError:
        return "MISSING", 0, 0
    huge = [line for line in text.splitlines() if "huge" in line]
    return (
        " | ".join(huge[:3]),
        int(any("N2=" in line for line in huge)),
        int(any("N0=" in line for line in huge)),
    )


def parse_perf(stderr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in stderr.splitlines():
        parts = line.split(",")
        if len(parts) < 3:
            continue
        raw = parts[0].strip()
        event = parts[2].strip().lower()
        if raw.startswith("<"):
            counts[event] = -1
            continue
        try:
            counts[event] = int(float(raw))
        except ValueError:
            pass
    return counts


def parse_latency(stdout: str) -> tuple[float, int, float]:
    data = json.loads(stdout)
    row = data[0]
    cycles = float(row["cycles_per_load"])
    tsc_hz = int(row["tsc_hz"])
    return cycles, tsc_hz, cycles / tsc_hz * 1e9


def main() -> int:
    cmd = [
        "perf", "stat", "-x,", "-e", ",".join(EVENTS), "--",
        "numactl", "--membind=2", "--cpunodebind=0", "--",
        str(VICTIM),
        "--cpu", "0",
        "--node", "2",
        "--wss", str(512 * 1024 * 1024),
        "--trials", "1",
        "--run-sec", "10",
    ]
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    victim_pid = find_pointer_chase(proc.pid)
    numa_sample, huge_n2, huge_n0 = read_numa(victim_pid)
    stdout, stderr = proc.communicate(timeout=60)
    if proc.returncode != 0:
        print(stderr)
        return proc.returncode

    counts = parse_perf(stderr)
    cycles, tsc_hz, latency_ns = parse_latency(stdout)
    l3_hit = counts.get("mem_load_retired.l3_hit", 0)
    l3_miss = counts.get("mem_load_retired.l3_miss", 0)
    denom = l3_hit + l3_miss
    l3_miss_fraction = l3_miss / denom if denom > 0 else 0.0
    row = {
        "case": "CXL_512MB_pmu",
        "wss_bytes": 512 * 1024 * 1024,
        "wss_mb": 512,
        "llc_capacity_mb": 60,
        "latency_ns": round(latency_ns, 2),
        "cycles_per_load": round(cycles, 3),
        "tsc_hz": tsc_hz,
        "l1_hit": counts.get("mem_load_retired.l1_hit", 0),
        "l2_hit": counts.get("mem_load_retired.l2_hit", 0),
        "l3_hit": l3_hit,
        "l3_miss": l3_miss,
        "longest_lat_cache_miss": counts.get("longest_lat_cache.miss", 0),
        "l3_miss_fraction": round(l3_miss_fraction, 6),
        "numa_huge_n2": huge_n2,
        "numa_huge_n0": huge_n0,
        "numa_maps_sample": numa_sample,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
