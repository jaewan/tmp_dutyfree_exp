#!/usr/bin/env bash
# identify_ccds.sh -- Topology helper for AMD CCD-aware experiments.
#
# Prints one-logical-per-physical-core mapping by CCD(L3) and emits
# ready-to-use core lists for same-CCD and same-socket inter-CCD placements.
set -euo pipefail

NODE="${NODE:-${AGG_NUMA_NODE:-1}}"
AGG_THREADS="${AGG_THREADS:-16}"

echo "=== Core-to-CCD Mapping (NUMA node $NODE, physical cores only) ==="

eval "$(NODE="$NODE" AGG_THREADS="$AGG_THREADS" python3 - <<'PY'
import os, subprocess, collections, sys
node = int(os.environ.get('NODE', '1'))
agg_threads = int(os.environ.get('AGG_THREADS', '16'))
out = subprocess.check_output(['lscpu', '-e'], text=True)
rows = [ln for ln in out.splitlines()[1:] if ln.strip()]
phys = {}
for ln in rows:
    p = ln.split()
    if len(p) < 5:
        continue
    cpu,node_id,socket,core = map(int, p[:4])
    if node_id != node:
        continue
    toks = p[4].split(':')
    l3 = int(toks[3]) if len(toks) >= 4 and toks[3].isdigit() else -1
    key = (socket, core)
    if key not in phys or cpu < phys[key][0]:
        phys[key] = (cpu, node_id, socket, core, l3)
entries = sorted(phys.values(), key=lambda x: x[0])
if not entries:
    print('echo "No physical cores found on node %d"; exit 1' % node)
    sys.exit(0)

by_l3 = collections.OrderedDict()
for e in sorted(entries, key=lambda x: (x[4], x[0])):
    by_l3.setdefault(e[4], []).append(e[0])

print('echo "CPU SOCKET CORE L3"')
for e in entries[:128]:
    print(f'echo "{e[0]:>3} {e[2]:>6} {e[3]:>4} {e[4]:>3}"')

print('echo ""')
print('echo "Cores per CCD(L3):"')
for l3, cpus in by_l3.items():
    s = ','.join(str(c) for c in cpus[:8])
    print(f'echo "  L3_{l3}: {len(cpus)} cores  first={s}"')

# Recommendations
l3_keys = list(by_l3.keys())
victim_ccd = l3_keys[0]
victim_core = by_l3[victim_ccd][0]
same_ccd_agg = [c for c in by_l3[victim_ccd] if c != victim_core][:agg_threads]
inter_ccd_pool = []
for l3 in l3_keys[1:]:
    inter_ccd_pool.extend(by_l3[l3])
inter_ccd_agg = inter_ccd_pool[:agg_threads]

print('echo ""')
print('echo "Recommended placements:"')
print(f'echo "  victim_core={victim_core} victim_ccd={victim_ccd}"')
print(f'echo "  same_ccd_aggressors={",".join(map(str, same_ccd_agg))}"')
print(f'echo "  inter_ccd_aggressors={",".join(map(str, inter_ccd_agg))}"')
print('echo ""')
print('echo "Suggested exp2 env:"')
print(f'echo "  TOPOLOGY=same_socket_inter_ccd AGG_NUMA_NODE={node} AGG_THREADS={agg_threads} VICTIM_CORE={victim_core}"')
PY
)"
