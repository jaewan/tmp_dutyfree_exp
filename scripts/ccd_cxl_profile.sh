#!/usr/bin/env bash
# ccd_cxl_profile.sh -- Profile WB CXL bandwidth by CCD on a NUMA node.
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
AGG_NODE="${AGG_NUMA_NODE:-1}"
DUR="${DURATION_SEC:-10}"
PMB="${PER_MB:-256}"
TPR="${THREADS_PER_CCD:-4}"
TIMEOUT_SINGLE="${TIMEOUT_SINGLE:-30}"
TIMEOUT_CUM="${TIMEOUT_CUM:-60}"

echo "===== CCD -> CXL Bandwidth Profile (node $AGG_NODE) ====="

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

AGG_NODE="$AGG_NODE" python3 - <<'PY' > "$tmp"
import subprocess, collections, os
node = int(os.environ.get('AGG_NODE', '1'))
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
by_l3 = collections.OrderedDict()
for e in sorted(entries, key=lambda x: (x[4], x[0])):
    by_l3.setdefault(e[4], []).append(e[0])
for l3, cpus in by_l3.items():
    print(f"{l3}:{','.join(str(c) for c in cpus)}")
PY

if [ ! -s "$tmp" ]; then
    echo "No physical cores discovered on node $AGG_NODE"
    exit 1
fi

echo ""
echo "=== CCD inventory ==="
printf "%-8s %-8s %s\n" "CCD" "#Cores" "First cores"
echo "------------------------------------------------"
while IFS=: read -r ccd cl; do
    IFS=',' read -ra arr <<< "$cl"
    preview=$(IFS=,; echo "${arr[*]:0:8}")
    printf "%-8s %-8s %s\n" "$ccd" "${#arr[@]}" "$preview"
done < "$tmp"

echo ""
echo "=== Per-CCD WB bandwidth (${TPR}t/CCD, ${DUR}s) ==="
printf "%-8s %-24s %12s\n" "CCD" "Cores" "WB GB/s"
echo "------------------------------------------------"
while IFS=: read -r ccd cl; do
    IFS=',' read -ra arr <<< "$cl"
    n=${#arr[@]}; [ "$n" -gt "$TPR" ] && n="$TPR"
    cores=$(IFS=,; echo "${arr[*]:0:$n}")
    of=$(mktemp)
    timeout "${TIMEOUT_SINGLE}s" "$BINDIR/aggressor" -m wb_load -t "$n" -c "$cores" -s "$PMB" -d "$DUR" > "$of" 2>&1 || true
    bw=$(grep '^RESULT' "$of" | grep -oP 'bw_gbps=\K[0-9.]+' || echo "N/A")
    printf "%-8s %-24s %12s\n" "$ccd" "${cores:0:24}" "$bw"
    rm -f "$of"
done < "$tmp"

echo ""
echo "=== Cross-CCD cumulative WB scaling (${TPR}t/CCD) ==="
printf "%-6s %-8s %12s %10s\n" "#CCD" "#Thr" "WB GB/s" "Per-CCD"
echo "------------------------------------------------"
ccum=""
cc=0
while IFS=: read -r ccd cl; do
    IFS=',' read -ra arr <<< "$cl"
    n=${#arr[@]}; [ "$n" -gt "$TPR" ] && n="$TPR"
    add=$(IFS=,; echo "${arr[*]:0:$n}")
    [ -z "$ccum" ] && ccum="$add" || ccum="$ccum,$add"
    cc=$((cc + 1))
    tt=$((cc * TPR))
    of=$(mktemp)
    timeout "${TIMEOUT_CUM}s" "$BINDIR/aggressor" -m wb_load -t "$tt" -c "$ccum" -s "$PMB" -d "$DUR" > "$of" 2>&1 || true
    bw=$(grep '^RESULT' "$of" | grep -oP 'bw_gbps=\K[0-9.]+' || echo "N/A")
    if [ "$bw" != "N/A" ]; then
        pc=$(python3 - <<PY
bw=float("$bw")
cc=int("$cc")
print(f"{bw/cc:.2f}")
PY
)
    else
        pc="N/A"
    fi
    printf "%-6s %-8s %12s %10s\n" "$cc" "$tt" "$bw" "$pc"
    rm -f "$of"
done < "$tmp"

echo ""
echo "Suggested next step: run exp2 with TOPOLOGY=same_socket_inter_ccd and AGG_NUMA_NODE=$AGG_NODE"
