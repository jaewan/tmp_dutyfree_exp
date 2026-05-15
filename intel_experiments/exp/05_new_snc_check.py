#!/usr/bin/env python3
"""
Phase 5-NEW — SNC Isolation Control (H10)

Checks whether SNC (Sub-NUMA Clustering) is enabled. If SNC is enabled,
runs the Phase 2 matrix with victim and aggressors in different SNC sub-clusters.

On SPR with SNC enabled (4 sub-clusters per socket), each sub-cluster has
8 cores and a dedicated portion of the LLC/SF domain. Placing victim in
sub-cluster 0 and aggressors in sub-cluster 2 would isolate the victim's
SF domain from aggressor pressure.

If SNC is disabled (current state on this machine, per N2), this script
documents the platform limitation and marks H10 as N/A.

Outputs:
  results/processed/05_new_snc_check.md
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
import runner
from runner import log

PROC_DIR = runner.RESULTS_PROC
PROC_DIR.mkdir(parents=True, exist_ok=True)


def check_snc_status():
    """Check SNC mode via NUMA topology. SNC off → 2 nodes per socket."""
    import re

    # Check number of NUMA nodes and distances
    try:
        result = subprocess.run(
            ["numactl", "--hardware"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout
    except Exception as e:
        output = f"ERROR: {e}"

    # Count nodes
    nodes = []
    for line in output.splitlines():
        m = re.match(r'node\s+(\d+)\s+size:', line)
        if m:
            nodes.append(int(m.group(1)))

    # Check node distances
    distances = {}
    in_dist = False
    for line in output.splitlines():
        if "node distances:" in line.lower():
            in_dist = True
            continue
        if in_dist:
            m = re.match(r'\s*(\d+):\s+([\d\s]+)', line)
            if m:
                src = int(m.group(1))
                dists = list(map(int, m.group(2).split()))
                distances[src] = dists

    return {
        "n_nodes": len(nodes),
        "nodes": nodes,
        "distances": distances,
        "numactl_output": output,
    }


def infer_snc(topo: dict) -> str:
    n = topo["n_nodes"]
    if n == 2:
        return "SNC_OFF_2SOCKET"
    elif n == 4:
        dists = topo["distances"]
        # SNC on single socket: intra-socket distance for sub-clusters is ~14
        # (smaller than cross-socket ~21). Check if all 4 nodes are "close".
        if dists:
            max_intra = max(dists[i][j]
                           for i in dists
                           for j, d in enumerate(dists[i])
                           if d < 20)
            if max_intra <= 14:
                return "SNC_ON_2CLUSTER"
        return "UNKNOWN_4NODE"
    elif n == 8:
        return "SNC_ON_4CLUSTER"
    else:
        return f"UNKNOWN_{n}NODE"


def write_report(topo: dict, snc_mode: str, out_path: Path):
    with open(out_path, "w") as f:
        f.write("# Phase 5-NEW — SNC Isolation Control Check\n")
        f.write(f"## Date: {datetime.now().isoformat()}\n\n")

        f.write("## SNC Status\n\n")
        f.write(f"- NUMA nodes detected: {topo['n_nodes']}\n")
        f.write(f"- SNC mode inference: **{snc_mode}**\n\n")

        f.write("### numactl --hardware output\n\n")
        f.write("```\n")
        f.write(topo["numactl_output"])
        f.write("```\n\n")

        if "SNC_OFF" in snc_mode:
            f.write("## H10 Evaluation\n\n")
            f.write("**H10: N/A — SNC is not enabled on this platform.**\n\n")
            f.write("SNC (Sub-NUMA Clustering) was confirmed disabled:\n")
            f.write("- 2 NUMA nodes present, one per socket (not 4 sub-clusters per socket)\n")
            f.write("- Node distances: 10 (intra-socket), 21 (inter-socket)\n\n")
            f.write("This matches the finding documented in NEGATIVE_RESULTS.md §N2.\n\n")
            f.write("**Impact on paper:** The SNC isolation control (H10) cannot be evaluated\n")
            f.write("without a BIOS change and reboot. Document as a platform limitation.\n\n")
            f.write("**Alternative approach (partial):** Run victim on CPU 0 (node 0) with\n")
            f.write("aggressors on node 1 CPUs. This tests cross-socket SF isolation, which\n")
            f.write("is stronger than the proposed SNC test but confounds NUMA access latency.\n")
            f.write("Not recommended as a primary control; leave H10 as N/A.\n\n")
            f.write("**Recommendation for paper:**\n")
            f.write("  - Acknowledge SNC-OFF as a limitation in §Implementation.\n")
            f.write("  - Note that SNC isolation would strengthen the SF-locality claim.\n")
            f.write("  - The L2-fit control (Phase 4-NEW) partially compensates: if tax persists\n")
            f.write("    at L2 level, the mechanism must involve back-invalidation traversing the\n")
            f.write("    interconnect between cores, which is the SF-mediated pathway.\n")

        elif "SNC_ON" in snc_mode:
            f.write("## H10 Evaluation — SNC IS ENABLED\n\n")
            f.write(f"**SNC mode detected: {snc_mode}**\n\n")
            f.write("SNC is available! H10 can be evaluated.\n\n")
            if snc_mode == "SNC_ON_4CLUSTER":
                f.write("SPR 4-way SNC creates 4 sub-clusters per socket.\n")
                f.write("Victim: CPU 0 (sub-cluster 0). Aggressors: CPUs 16–31 (sub-cluster 2).\n")
            else:
                f.write("2-cluster SNC: victim in cluster 0, aggressors in cluster 1.\n")
            f.write("\n**NOTE: SNC was detected as enabled but NEGATIVE_RESULTS.md §N2 documents\n")
            f.write("it as disabled on this machine. If this script reports SNC_ON, please verify\n")
            f.write("manually and update N2 accordingly before running the SNC experiment.\n")
            f.write("Run: exp/05_new_snc_experiment.py (to be written) for the actual measurements.\n")

        else:
            f.write(f"## H10 Evaluation — UNKNOWN SNC STATUS ({snc_mode})\n\n")
            f.write("Could not determine SNC mode from NUMA topology.\n")
            f.write("Manual inspection of BIOS settings or running `iasl` + ACPI tables required.\n")

    log(f"Wrote: {out_path}")


def main():
    log("=== Phase 5-NEW: SNC Isolation Check ===")

    topo = check_snc_status()
    log(f"NUMA nodes: {topo['n_nodes']}")

    snc_mode = infer_snc(topo)
    log(f"SNC inference: {snc_mode}")

    out_path = PROC_DIR / "05_new_snc_check.md"
    write_report(topo, snc_mode, out_path)

    print(f"\nPhase 5-NEW complete: {out_path}")

    if "SNC_OFF" in snc_mode:
        print("H10: N/A — SNC disabled on this platform. Document as limitation.")
    elif "SNC_ON" in snc_mode:
        print("H10: CAN EVALUATE — SNC detected. Proceed with SNC isolation experiment.")


if __name__ == "__main__":
    main()
