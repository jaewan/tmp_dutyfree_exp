#!/usr/bin/env python3
"""Phase 19.0 prefetcher ablation for node-2 512 MB pointer chase."""

import csv
import json
import statistics
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "processed" / "19.0_prefetcher_ablation.csv"
VICTIM = ROOT / "bench" / "victim" / "pointer_chase"

TRIALS = 10
WSS = 512 * 1024 * 1024
RUN_SEC = 10


def read_numa(pid: int) -> tuple[str, int, int]:
    try:
        text = Path(f"/proc/{pid}/numa_maps").read_text()
    except FileNotFoundError:
        return "MISSING", 0, 0
    except PermissionError:
        return "PERMISSION_DENIED_CAP_SYS_RAWIO_PROCESS", -1, -1
    huge = [line for line in text.splitlines() if "huge" in line]
    return (
        " | ".join(huge[:3]),
        int(any("N2=" in line for line in huge)),
        int(any("N0=" in line for line in huge)),
    )


def run_one(label: str, pf_disable: bool, trial: int) -> dict:
    cmd = [
        "numactl", "--membind=2", "--cpunodebind=0", "--",
        str(VICTIM),
        "--cpu", "0",
        "--node", "2",
        "--wss", str(WSS),
        "--trials", "1",
        "--run-sec", str(RUN_SEC),
    ]
    if pf_disable:
        cmd.append("--pf-disable")
    proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.5)
    numa_sample, n2, n0 = read_numa(proc.pid)
    stdout, stderr = proc.communicate(timeout=RUN_SEC + 30)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} trial={trial} failed: {stderr[-500:]}")
    row = json.loads(stdout)[0]
    cycles = float(row["cycles_per_load"])
    tsc_hz = int(row["tsc_hz"])
    return {
        "case": label,
        "trial": trial,
        "wss_mb": WSS // (1024 * 1024),
        "node": 2,
        "prefetcher_state": "off" if pf_disable else "on",
        "latency_ns": round(cycles / tsc_hz * 1e9, 2),
        "cycles_per_load": round(cycles, 3),
        "tsc_hz": tsc_hz,
        "numa_huge_n2": n2,
        "numa_huge_n0": n0,
        "numa_maps_sample": numa_sample,
        "stderr_summary": " ".join(stderr.strip().splitlines()[:4]),
    }


def iqr(vals: list[float]) -> float:
    q = statistics.quantiles(vals, n=4, method="inclusive")
    return q[2] - q[0]


def main() -> int:
    rows = []
    for label, pf_disable in [("CXL_512MB_pf_on", False), ("CXL_512MB_pf_off", True)]:
        for trial in range(TRIALS):
            row = run_one(label, pf_disable, trial)
            rows.append(row)
            print(f"{label} trial={trial} latency_ns={row['latency_ns']} "
                  f"n2={row['numa_huge_n2']}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    for label in sorted(set(r["case"] for r in rows)):
        vals = [float(r["latency_ns"]) for r in rows if r["case"] == label]
        print(f"SUMMARY {label}: median_ns={statistics.median(vals):.2f} "
              f"iqr_ns={iqr(vals):.2f} min={min(vals):.2f} max={max(vals):.2f}")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
