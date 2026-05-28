#!/usr/bin/env python3
"""
mitigation_common.py — shared helpers for the Comprehensive Real-Hardware
Mitigation Trap Plan (Phase 1~3).

All phases emit per-trial CSV rows sharing one superset schema (FIELDNAMES) so
the Phase 5 synthesis plot can concatenate them and plot
  X = aggregate_bw_gbps (CXL bandwidth)
  Y = victim_slowdown_pct (== tax_pct)
colored by phase/knob.

Topology (see runner.py): the aggressor streams from the CXL NUMA node while
victim+aggressors are co-located on that node's host socket so the streaming
fills contend with the victim in that socket's shared LLC.
"""

import csv
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import runner
from runner import log, AggressorProcess, VictimRun

# Common CSV schema (superset; per-phase scripts fill the relevant columns).
FIELDNAMES = [
    "phase", "knob", "knob_value", "condition", "n_aggr_cores", "trial",
    "cycles_per_load", "quiescent_cycles", "tax_pct", "aggregate_bw_gbps",
    "victim_slowdown_pct", "page_kb", "readonly", "notes",
]

NODE      = runner.NUMA_NODE       # victim cores + victim DRAM (socket-local)
AGGR_NODE = runner.AGGR_MEM_NODE   # aggressor stream memory (the CXL node)


def measure_quiescent(victim_wss: int, *, trials: int = 10, run_sec: float = 1.0,
                      node: int = NODE, victim_cpu: int = runner.VICTIM_CPU,
                      victim_page_kb: Optional[int] = None,
                      warmup_trials: int = 0) -> float:
    """Quiescent victim baseline (no aggressors): mean cycles/load."""
    v = VictimRun(cpu=victim_cpu, node=node, wss=victim_wss,
                  trials=trials, run_sec=run_sec, page_kb=victim_page_kb,
                  warmup_trials=warmup_trials)
    tr = v.run()
    if not tr:
        return 0.0
    return statistics.mean(t["cycles_per_load"] for t in tr)


def measure_point(condition: str, n_aggr_cores: int, *, victim_wss: int,
                  trials: int, run_sec: float = 1.0, warmup: float = 5.0,
                  node: int = NODE, victim_cpu: int = runner.VICTIM_CPU,
                  aggr_cpus: Optional[List[int]] = None, region_gb: int = 1,
                  agg_page_kb: Optional[int] = None, readonly: bool = False,
                  victim_page_kb: Optional[int] = None, aggr_node: int = AGGR_NODE,
                  warmup_trials: int = 0
                  ) -> Tuple[List[Dict], float]:
    """Run `n_aggr_cores` aggressors of `condition` co-located with the victim,
    then run the victim for `trials` trials. Returns (trial_results, agg_bw_gbps).

    Aggressors run on `node`'s socket cores but stream memory from `aggr_node`
    (the CXL node). The victim runs on `node` with its DRAM local to that socket.
    Aggressors stay up for the whole victim measurement window. `agg_page_kb`/
    `readonly` are only honored by condition A (stream_wb)."""
    if aggr_cpus is None:
        aggr_cpus = runner.AGGR_CPUS
    total_dur = warmup + (warmup_trials + trials) * (run_sec + 0.1) + 10.0

    aggressors: List[AggressorProcess] = []
    for i in range(n_aggr_cores):
        a = AggressorProcess(condition, cpu=aggr_cpus[i], region_gb=region_gb,
                             duration_sec=total_dur, node=aggr_node,
                             page_kb=agg_page_kb, readonly=readonly)
        a.start()
        aggressors.append(a)

    import signal as _sig
    agg_bw = 0.0
    trial_results: List[Dict] = []
    try:
        runner.warmup_sleep(warmup)
        victim = VictimRun(cpu=victim_cpu, node=node, wss=victim_wss,
                           trials=trials, run_sec=run_sec, page_kb=victim_page_kb,
                           warmup_trials=warmup_trials)
        trial_results = victim.run()
    finally:
        # Terminate aggressors and collect their accurate JSON avg_bw_gbps
        # (stderr per-report bw is unreliable: it is bytes-per-pass / interval).
        for a in aggressors:
            if a.proc and a.proc.poll() is None:
                a.proc.send_signal(_sig.SIGTERM)
        bws = []
        for a in aggressors:
            bw = a.read_final_bw()
            if bw:
                bws.append(bw)
        agg_bw = sum(bws)
        for a in aggressors:
            a.stop()
    runner.cooldown_sleep(2.0)
    return trial_results, agg_bw


def make_rows(trial_results: List[Dict], *, phase: str, knob: str, knob_value,
              condition: str, n_aggr_cores: int, quiescent: float,
              agg_bw: float, page_kb=None, readonly=None, notes: str = "") -> List[Dict]:
    """Annotate per-trial victim results into the common schema."""
    rows = []
    for tr in trial_results:
        cyc = tr["cycles_per_load"]
        tax = round((cyc - quiescent) / quiescent * 100, 3) if quiescent > 0 else 0.0
        rows.append({
            "phase": phase,
            "knob": knob,
            "knob_value": knob_value,
            "condition": condition,
            "n_aggr_cores": n_aggr_cores,
            "trial": tr["trial"],
            "cycles_per_load": round(cyc, 3),
            "quiescent_cycles": round(quiescent, 3),
            "tax_pct": tax,
            "aggregate_bw_gbps": round(agg_bw, 3),
            "victim_slowdown_pct": tax,
            "page_kb": page_kb if page_kb is not None else "",
            "readonly": readonly if readonly is not None else "",
            "notes": notes,
        })
    return rows


def summarize(rows: List[Dict]) -> Dict[str, Dict]:
    """Group rows by (knob_value, condition) → mean tax + mean BW."""
    from collections import defaultdict
    grp = defaultdict(lambda: {"tax": [], "bw": [], "cyc": []})
    for r in rows:
        key = (r["knob_value"], r["condition"])
        grp[key]["tax"].append(r["tax_pct"])
        grp[key]["bw"].append(r["aggregate_bw_gbps"])
        grp[key]["cyc"].append(r["cycles_per_load"])
    out = {}
    for key, d in grp.items():
        out[key] = {
            "n": len(d["tax"]),
            "tax_mean": round(statistics.mean(d["tax"]), 3),
            "tax_median": round(statistics.median(d["tax"]), 3),
            "tax_std": round(statistics.stdev(d["tax"]), 3) if len(d["tax"]) > 1 else 0.0,
            "bw_mean": round(statistics.mean(d["bw"]), 3),
            "cyc_mean": round(statistics.mean(d["cyc"]), 3),
        }
    return out


def write_csv(rows: List[Dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log(f"Wrote: {path}")
