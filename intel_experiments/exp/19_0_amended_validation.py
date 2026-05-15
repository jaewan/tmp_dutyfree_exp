#!/usr/bin/env python3
"""Phase 19.0 amended CXL latency validation.

Runs node-2 pointer-chase at 512 MB (gating) and 64 MB (record-only), with
one benchmark process per trial so /proc/PID/numa_maps can be inspected before
the measurement completes.
"""

import csv
import json
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.0_calibration_amended.csv"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase"

TRIALS = 10
RUN_SEC = 10
CPU = 0
NODE = 2
CASES = [
    ("CXL_512MB_gate", 512 * 1024 * 1024, True),
    ("CXL_64MB_record", 64 * 1024 * 1024, False),
]


def read_numa_maps(pid: int) -> tuple[str, bool, bool]:
    path = Path(f"/proc/{pid}/numa_maps")
    try:
        text = path.read_text()
    except FileNotFoundError:
        return "MISSING", False, False

    huge_lines = [line for line in text.splitlines() if "huge" in line]
    sample = " | ".join(huge_lines[:3])
    has_n2_huge = any("huge" in line and "N2=" in line for line in huge_lines)
    has_n0_huge = any("huge" in line and "N0=" in line for line in huge_lines)
    return sample, has_n2_huge, has_n0_huge


def parse_result(stdout: str) -> dict:
    data = json.loads(stdout)
    if not isinstance(data, list) or not data:
        raise ValueError("pointer_chase did not return a non-empty JSON array")
    return data[0]


def run_one(label: str, wss: int, trial: int) -> dict:
    cmd = [
        "numactl", "--membind=2", "--cpunodebind=0", "--",
        str(VICTIM),
        "--cpu", str(CPU),
        "--node", str(NODE),
        "--wss", str(wss),
        "--trials", "1",
        "--run-sec", str(RUN_SEC),
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=ROOT)
    time.sleep(1.5)
    numa_sample, has_n2_huge, has_n0_huge = read_numa_maps(proc.pid)
    stdout, stderr = proc.communicate(timeout=RUN_SEC + 30)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} trial {trial} failed rc={proc.returncode}: {stderr[-500:]}")

    parsed = parse_result(stdout)
    cycles = float(parsed["cycles_per_load"])
    tsc_hz = float(parsed["tsc_hz"])
    latency_ns = cycles / tsc_hz * 1e9
    return {
        "case": label,
        "trial": trial,
        "node": NODE,
        "cpu": CPU,
        "wss_bytes": wss,
        "wss_mb": wss // (1024 * 1024),
        "run_sec": RUN_SEC,
        "cycles_per_load": round(cycles, 3),
        "tsc_hz": int(tsc_hz),
        "latency_ns": round(latency_ns, 2),
        "numa_huge_n2": int(has_n2_huge),
        "numa_huge_n0": int(has_n0_huge),
        "numa_maps_sample": numa_sample,
        "stderr_summary": " ".join(stderr.strip().splitlines()[:2]),
    }


def iqr(vals: list[float]) -> float:
    q = statistics.quantiles(vals, n=4, method="inclusive")
    return q[2] - q[0]


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for label, wss, _is_gate in CASES:
        for trial in range(TRIALS):
            row = run_one(label, wss, trial)
            rows.append(row)
            print(f"{label} trial={trial} latency_ns={row['latency_ns']} "
                  f"cycles={row['cycles_per_load']} n2={row['numa_huge_n2']}",
                  flush=True)

    fieldnames = list(rows[0].keys())
    with OUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    for label, _wss, is_gate in CASES:
        vals = [float(r["latency_ns"]) for r in rows if r["case"] == label]
        med = statistics.median(vals)
        spread = iqr(vals)
        print(f"SUMMARY {label}: median_ns={med:.2f} iqr_ns={spread:.2f}")
        if is_gate and not (300.0 <= med <= 600.0):
            print(f"HALT: amended validation median {med:.2f} ns outside [300,600]")
            return 2

    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
