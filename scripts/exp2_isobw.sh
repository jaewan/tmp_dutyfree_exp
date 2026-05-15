#!/usr/bin/env bash
#
# exp2_isobw.sh — Iso-bandwidth victim degradation with topology control.
#
# Default behavior is continuous, no-throttle iso-BW via thread-count matching
# implemented in scripts/exp2_isobw_continuous.sh.
#
# Legacy throttled-WB path is kept only for historical reproducibility and must
# be explicitly enabled with ISO_METHOD=legacy_throttle.
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
ISO_METHOD="${ISO_METHOD:-continuous}"

if [ "$ISO_METHOD" != "legacy_throttle" ]; then
    exec "$(dirname "$0")/exp2_isobw_continuous.sh"
fi

OUTDIR="${OUTDIR:-./results/exp2}"
if [ -d "$OUTDIR" ] && ! rm -rf "$OUTDIR" 2>/dev/null; then
    ts=$(date +%Y%m%d_%H%M%S)
    OUTDIR="${OUTDIR}_${ts}"
    echo "WARN: could not clean old output dir; using $OUTDIR"
fi
if ! mkdir -p "$OUTDIR" 2>/dev/null; then
    ts=$(date +%Y%m%d_%H%M%S)
    OUTDIR="/tmp/$(basename "$OUTDIR")_${ts}_$$"
    echo "WARN: local results dir not writable; using $OUTDIR"
    mkdir -p "$OUTDIR"
fi

TOPOLOGY="${TOPOLOGY:-same_socket_inter_ccd}"   # same_socket_inter_ccd|same_ccd|inter_socket
CXL_NODE="${CXL_NUMA_NODE:-2}"
AGG_NODE="${AGG_NUMA_NODE:-1}"
VICTIM_NODE_OVERRIDE="${VICTIM_NUMA_NODE:-}"
VICTIM_CORE_OVERRIDE="${VICTIM_CORE:-}"
AGG_CORE_LIST_OVERRIDE="${AGG_CORE_LIST:-}"   # optional explicit comma-separated core list

AGG_THREADS="${AGG_THREADS:-16}"
PER_MB="${PER_MB:-256}"
MATCH_WORKING_SET="${MATCH_WORKING_SET:-1}"

NUM_RUNS="${NUM_RUNS:-30}"
WARMUP="${WARMUP_SEC:-5}"
MEASURE="${MEASURE_SEC:-15}"
TIMEOUT_S="${TIMEOUT_S:-180}"
RANDOM_SEED="${RANDOM_SEED:-20260305}"
BW_TOL_PCT="${BW_TOL_PCT:-3}"
VICTIM_WS_KB="${VICTIM_WS_KB:-}"
PRECHECK_EXP1_SANITY="${PRECHECK_EXP1_SANITY:-1}"
PRECHECK_DURATION_SEC="${PRECHECK_DURATION_SEC:-8}"
PRECHECK_TOL_PCT="${PRECHECK_TOL_PCT:-$BW_TOL_PCT}"

# Optional manual override if exp1 parsing is unavailable.
WC_TARGET_BW_GBPS="${WC_TARGET_BW_GBPS:-}"
ISO_WB_THROTTLE_MBPS="${ISO_WB_THROTTLE_MBPS:-}"

if [ ! -x "$BINDIR/victim" ] || [ ! -x "$BINDIR/aggressor" ]; then
    echo "ERROR: Missing binaries under $BINDIR (need victim/aggressor)."
    exit 1
fi

if [ ! -c /dev/cxl_wc ]; then
    echo "ERROR: /dev/cxl_wc is required for iso-bandwidth WC vs WB comparison."
    exit 1
fi

if [ -z "$VICTIM_WS_KB" ]; then
    l2b=$(getconf LEVEL2_CACHE_SIZE 2>/dev/null || echo "")
    if [ -n "$l2b" ] && [ "$l2b" -gt 0 ] 2>/dev/null; then
        VICTIM_WS_KB=$(( (l2b * 3 / 4) / 1024 / 3 ))
    else
        VICTIM_WS_KB=256
    fi
fi

# -------------------------------------------------------------------
# Topology discovery: choose physical cores and placement-safe lists.
# -------------------------------------------------------------------
eval "$(TOPOLOGY="$TOPOLOGY" AGG_NODE="$AGG_NODE" CXL_NODE="$CXL_NODE" AGG_THREADS="$AGG_THREADS" VICTIM_NODE_OVERRIDE="$VICTIM_NODE_OVERRIDE" VICTIM_CORE_OVERRIDE="$VICTIM_CORE_OVERRIDE" AGG_CORE_LIST_OVERRIDE="$AGG_CORE_LIST_OVERRIDE" python3 - <<'PY'
import os, subprocess, sys

topology = os.environ.get('TOPOLOGY', 'same_socket_inter_ccd')
agg_node = int(os.environ.get('AGG_NODE', '1'))
cxl_node = int(os.environ.get('CXL_NODE', '2'))
agg_threads = int(os.environ.get('AGG_THREADS', '16'))
victim_node_override = os.environ.get('VICTIM_NODE_OVERRIDE', '').strip()
victim_core_override = os.environ.get('VICTIM_CORE_OVERRIDE', '').strip()
agg_core_list_override = os.environ.get('AGG_CORE_LIST_OVERRIDE', '').strip()

out = subprocess.check_output(['lscpu', '-e'], text=True)
lines = [ln for ln in out.splitlines() if ln.strip()]
if len(lines) < 2:
    print('echo "ERROR: lscpu -e returned no topology"; exit 1')
    sys.exit(0)

# Parse rows: CPU NODE SOCKET CORE L1d:L1i:L2:L3 ...
rows = []
for ln in lines[1:]:
    p = ln.split()
    if len(p) < 5:
        continue
    cpu = int(p[0]); node = int(p[1]); socket = int(p[2]); core = int(p[3])
    cache = p[4]
    toks = cache.split(':')
    l3 = int(toks[3]) if len(toks) >= 4 and toks[3].isdigit() else -1
    rows.append((cpu, node, socket, core, l3))

if not rows:
    print('echo "ERROR: Could not parse lscpu topology"; exit 1')
    sys.exit(0)

# Keep one logical CPU per physical core.
phys = {}
for cpu, node, socket, core, l3 in rows:
    key = (socket, core)
    if key not in phys or cpu < phys[key][0]:
        phys[key] = (cpu, node, socket, core, l3)
entries = sorted(phys.values(), key=lambda x: x[0])

nodes = sorted(set(e[1] for e in entries))
if agg_node not in nodes:
    print(f'echo "ERROR: AGG_NUMA_NODE={agg_node} has no physical cores"; exit 1')
    sys.exit(0)

by_cpu = {e[0]: e for e in entries}

def pick_victim_node():
    if victim_node_override:
        vn = int(victim_node_override)
        if vn not in nodes:
            raise RuntimeError(f'VICTIM_NUMA_NODE={vn} has no physical cores')
        return vn
    if topology == 'inter_socket':
        cands = [n for n in nodes if n != agg_node and n != cxl_node]
        if not cands:
            cands = [n for n in nodes if n != agg_node]
        if not cands:
            raise RuntimeError('No victim node available for inter_socket')
        return min(cands)
    return agg_node

try:
    victim_node = pick_victim_node()
except Exception as e:
    print(f'echo "ERROR: {e}"; exit 1')
    sys.exit(0)

if victim_core_override:
    victim_core = int(victim_core_override)
    if victim_core not in by_cpu:
        print(f'echo "ERROR: VICTIM_CORE={victim_core} not found"; exit 1')
        sys.exit(0)
    vrec = by_cpu[victim_core]
    victim_socket = vrec[2]
    victim_l3 = vrec[4]
else:
    vcands = [e for e in entries if e[1] == victim_node]
    if not vcands:
        print(f'echo "ERROR: No victim core candidates on node {victim_node}"; exit 1')
        sys.exit(0)
    # Pick lowest CPU on victim node (stable).
    vrec = sorted(vcands, key=lambda x: x[0])[0]
    victim_core, _, victim_socket, _, victim_l3 = vrec

agg_cands = [e for e in entries if e[1] == agg_node]
if not agg_cands:
    print(f'echo "ERROR: No aggressor cores on node {agg_node}"; exit 1')
    sys.exit(0)

if topology == 'same_socket_inter_ccd':
    # Aggressors must be on same socket but different CCD/L3.
    agg_cands = [e for e in agg_cands if e[2] == victim_socket and e[4] != victim_l3]
elif topology == 'same_ccd':
    agg_cands = [e for e in agg_cands if e[2] == victim_socket and e[4] == victim_l3 and e[0] != victim_core]
elif topology == 'inter_socket':
    # Aggressors stay on AGG_NODE; victim should be on different socket by node choice.
    agg_cands = [e for e in agg_cands if e[0] != victim_core]
else:
    print(f'echo "ERROR: Unknown TOPOLOGY={topology}"; exit 1')
    sys.exit(0)

agg_cands = sorted(agg_cands, key=lambda x: (x[4], x[0]))
if agg_core_list_override:
    req = []
    for tok in agg_core_list_override.split(','):
        tok = tok.strip()
        if tok:
            req.append(int(tok))
    allowed = {e[0] for e in agg_cands}
    bad = [c for c in req if c not in allowed]
    if bad:
        print(f'echo "ERROR: AGG_CORE_LIST has cores violating TOPOLOGY constraints: {",".join(map(str,bad))}"; exit 1')
        sys.exit(0)
    if len(req) < agg_threads:
        print(f'echo "ERROR: AGG_CORE_LIST has {len(req)} cores but AGG_THREADS={agg_threads}"; exit 1')
        sys.exit(0)
    agg_cores = [str(c) for c in req[:agg_threads]]
else:
    if len(agg_cands) < agg_threads:
        print(f'echo "ERROR: Need {agg_threads} aggressor physical cores, found {len(agg_cands)} under TOPOLOGY={topology}"; exit 1')
        sys.exit(0)
    agg_cores = [str(e[0]) for e in agg_cands[:agg_threads]]

print(f'VICTIM_CORE={victim_core}')
print(f'VICTIM_NODE={victim_node}')
print(f'AGG_NODE={agg_node}')
print(f'AGG_CORES="{",".join(agg_cores)}"')
print(f'VICTIM_SOCKET={victim_socket}')
print(f'VICTIM_L3={victim_l3}')
PY
)"

echo "=============================================================="
echo " Experiment 2: Iso-BW Victim Degradation ($TOPOLOGY)"
echo "=============================================================="
echo "Victim   : core=$VICTIM_CORE node=$VICTIM_NODE socket=$VICTIM_SOCKET l3=$VICTIM_L3"
echo "Aggressor: node=$AGG_NODE threads=$AGG_THREADS cores=$AGG_CORES"
echo "Runs=$NUM_RUNS warmup=${WARMUP}s measure=${MEASURE}s"

device_mb() {
    local raw
    raw=$(cat /sys/module/cxl_memtype/parameters/cxl_phys_size 2>/dev/null || echo "")
    if [ -n "$raw" ] && [ "$raw" -gt 0 ] 2>/dev/null; then
        echo $((raw / 2 / 1024 / 1024))
    else
        echo 1024
    fi
}

WC_DEV_MB=$(device_mb)
PER_MB_WC=$PER_MB
if [ "$MATCH_WORKING_SET" = "1" ]; then
    NEED_MB=$((PER_MB * AGG_THREADS))
    if [ "$WC_DEV_MB" -lt "$NEED_MB" ]; then
        echo "ERROR: MATCH_WORKING_SET=1 requires WC device >= ${NEED_MB}MB (have ${WC_DEV_MB}MB)."
        echo "       Increase offlined CXL size (e.g., CXL_OFFLINE_GB>=16), or lower PER_MB/AGG_THREADS."
        exit 1
    fi
else
    PER_MB_WC=$((WC_DEV_MB / AGG_THREADS))
    [ "$PER_MB_WC" -lt 8 ] && PER_MB_WC=8
fi

echo "Working-set: WB=${PER_MB}MB/thread WC=${PER_MB_WC}MB/thread (WC device=${WC_DEV_MB}MB)"
echo "Victim WS : ${VICTIM_WS_KB}KB (derived from LEVEL2_CACHE_SIZE when unset)"

if [ -z "$WC_TARGET_BW_GBPS" ]; then
    # Prefer exact thread-count file from exp1, fallback to highest-thread file.
    EX1_EXACT="results/exp1/scale_wc_ntdqa_${AGG_THREADS}t.txt"
    if [ -f "$EX1_EXACT" ]; then
        WC_TARGET_BW_GBPS=$(grep '^RESULT' "$EX1_EXACT" | grep -oP 'bw_gbps=\K[0-9.]+' || true)
    fi
    if [ -z "$WC_TARGET_BW_GBPS" ]; then
        WC_TARGET_BW_GBPS=$(ls results/exp1/scale_wc_ntdqa_*t.txt 2>/dev/null | sort -V | tail -1 | xargs grep '^RESULT' 2>/dev/null | grep -oP 'bw_gbps=\K[0-9.]+' || true)
    fi
fi

if [ -z "$WC_TARGET_BW_GBPS" ]; then
    echo "ERROR: Could not determine WC target bandwidth from exp1; set WC_TARGET_BW_GBPS manually."
    exit 1
fi

if [ -z "$ISO_WB_THROTTLE_MBPS" ]; then
    ISO_WB_THROTTLE_MBPS=$(python3 - <<PY
bw = float("$WC_TARGET_BW_GBPS")
thr = int("$AGG_THREADS")
print(max(1, int(bw * 1000.0 / thr)))
PY
)
fi

echo "Target iso-bandwidth: WC=${WC_TARGET_BW_GBPS} GB/s, WB throttle/thread=${ISO_WB_THROTTLE_MBPS} MB/s"

if [ "$PRECHECK_EXP1_SANITY" = "1" ]; then
    echo ""
    echo "--- Precheck: exp1-style sanity on exact exp2 corelist ---"
    echo "Precheck cores=$AGG_CORES threads=$AGG_THREADS duration=${PRECHECK_DURATION_SEC}s"

    PRE_WC_OUT="$OUTDIR/precheck_wc_ntdqa.txt"
    PRE_WB_OUT="$OUTDIR/precheck_wb_load.txt"

    if ! timeout "${TIMEOUT_S}s" "$BINDIR/aggressor" \
        -m wc_ntdqa -t "$AGG_THREADS" -c "$AGG_CORES" -s "$PER_MB_WC" -d "$PRECHECK_DURATION_SEC" \
        > "$PRE_WC_OUT" 2>&1; then
        echo "ERROR: precheck wc_ntdqa failed; aborting."
        tail -20 "$PRE_WC_OUT" 2>/dev/null || true
        exit 1
    fi
    if ! timeout "${TIMEOUT_S}s" "$BINDIR/aggressor" \
        -m wb_load -t "$AGG_THREADS" -c "$AGG_CORES" -s "$PER_MB" -d "$PRECHECK_DURATION_SEC" \
        > "$PRE_WB_OUT" 2>&1; then
        echo "ERROR: precheck wb_load failed; aborting."
        tail -20 "$PRE_WB_OUT" 2>/dev/null || true
        exit 1
    fi

    PRE_WC_BW=$(grep '^RESULT' "$PRE_WC_OUT" | grep -oP 'bw_gbps=\K[0-9.]+' || true)
    PRE_WB_BW=$(grep '^RESULT' "$PRE_WB_OUT" | grep -oP 'bw_gbps=\K[0-9.]+' || true)
    if [ -z "$PRE_WC_BW" ] || [ -z "$PRE_WB_BW" ]; then
        echo "ERROR: precheck failed to parse bandwidth results; aborting."
        exit 1
    fi

    echo "Precheck BW: wc_ntdqa=${PRE_WC_BW} GB/s, wb_load=${PRE_WB_BW} GB/s"
    if ! python3 - <<PY
import sys
wc = float("$PRE_WC_BW")
target = float("$WC_TARGET_BW_GBPS")
tol = float("$PRECHECK_TOL_PCT")
err = abs((wc - target) / target) * 100.0 if target > 0 else 999.0
print(f"Precheck WC target error: {err:.2f}% (tol={tol:.2f}%)")
sys.exit(0 if err <= tol else 1)
PY
    then
        echo "ERROR: precheck WC bandwidth is outside tolerance on exact exp2 corelist."
        echo "       Adjust WC_TARGET_BW_GBPS / ISO_WB_THROTTLE_MBPS or rerun exp1 for this corelist."
        exit 1
    fi
fi

# Optional uncore events (if PMU exists)
UNCORE_EVENTS=""
if perf list 2>/dev/null | grep -q 'amd_l3'; then
    UNCORE_EVENTS="amd_l3/event=0x04,umask=0xff/"
elif perf list 2>/dev/null | grep -q 'unc_cha_dir_update'; then
    UNCORE_EVENTS="unc_cha_dir_update.any"
fi

PERF_EVENTS="cycles,instructions,r7064,r0864"
for ev in l2_pf_miss_l2_hit_l3.all l2_pf_miss_l2_l3.all; do
    if perf stat -e "$ev" -C "$VICTIM_CORE" sleep 0.05 >/dev/null 2>&1; then
        PERF_EVENTS+=",$ev"
    fi
done
echo "Victim perf events: $PERF_EVENTS"

run_baseline() {
    local run="$1"
    local out="$OUTDIR/run$run/baseline.txt"
    echo "  [baseline] run=$run"
    timeout "${TIMEOUT_S}s" "$BINDIR/victim" \
        -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" \
        -d "$MEASURE" -W "$WARMUP" > "$out" 2>&1 || true
    grep '^VICTIM' "$out" 2>/dev/null || echo "    (no baseline VICTIM line)"
}

run_scenario() {
    local name="$1"
    local mode="$2"
    local throttle="$3"
    local run="$4"
    local outpfx="$OUTDIR/run$run/$name"
    local sz_mb="$PER_MB"
    [ "$mode" = "wc_ntdqa" ] && sz_mb="$PER_MB_WC"

    echo "  [scenario] $name mode=$mode throttle=${throttle}MBps size=${sz_mb}MB/t"

    "$BINDIR/victim" \
        -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" \
        -d "$MEASURE" -W "$WARMUP" > "${outpfx}_victim.txt" 2>&1 &
    VPID=$!

    sleep "$WARMUP"

    local agg_extra=""
    [ "$throttle" != "0" ] && agg_extra="-R $throttle"
    "$BINDIR/aggressor" \
        -m "$mode" -t "$AGG_THREADS" -c "$AGG_CORES" -s "$sz_mb" -d "$MEASURE" $agg_extra \
        > "${outpfx}_aggressor.txt" 2>&1 &
    APID=$!

    local upid=""
    if [ -n "$UNCORE_EVENTS" ]; then
        perf stat -a -e "$UNCORE_EVENTS" -o "${outpfx}_uncore.txt" sleep "$MEASURE" &
        upid=$!
    fi

    sleep 1
    local ppid=""
    if kill -0 "$VPID" 2>/dev/null; then
        perf stat -p "$VPID" -e "$PERF_EVENTS" -o "${outpfx}_perf.txt" sleep $((MEASURE - 2)) &
        ppid=$!
    fi

    [ -n "$ppid" ] && wait "$ppid" 2>/dev/null || true
    [ -n "$upid" ] && wait "$upid" 2>/dev/null || true
    wait "$APID" 2>/dev/null || true
    wait "$VPID" 2>/dev/null || true

    local bw="N/A"
    bw=$(grep '^RESULT' "${outpfx}_aggressor.txt" 2>/dev/null | grep -oP 'bw_gbps=\K[0-9.]+' || echo "N/A")
    local vipc=""
    vipc=$(grep '^VICTIM' "${outpfx}_victim.txt" 2>/dev/null || true)
    echo "    BW=$bw  $vipc"
}

scenario_spec() {
    local sc="$1"
    case "$sc" in
        A_wc_ntdqa) echo "wc_ntdqa 0" ;;
        B_wb_isobw) echo "wb_load $ISO_WB_THROTTLE_MBPS" ;;
        C_wb_full)  echo "wb_load 0" ;;
        *) echo "ERROR" ;;
    esac
}

for run in $(seq 1 "$NUM_RUNS"); do
    mkdir -p "$OUTDIR/run$run"
    echo "=== Run $run / $NUM_RUNS ==="
    run_baseline "$run"

    order=$(python3 - <<PY
import random
sc = ['A_wc_ntdqa', 'B_wb_isobw', 'C_wb_full']
r = random.Random($RANDOM_SEED + $run)
print(' '.join(r.sample(sc, len(sc))))
PY
)
    echo "  order: $order"

    for sc in $order; do
        read -r mode throttle <<< "$(scenario_spec "$sc")"
        run_scenario "$sc" "$mode" "$throttle" "$run"
    done
    echo ""
done

python3 <<PY
import glob, os, re, statistics, math

outdir = "$OUTDIR"
num_runs = int("$NUM_RUNS")
wc_target = float("$WC_TARGET_BW_GBPS")
bw_tol = float("$BW_TOL_PCT")
scenarios = ['A_wc_ntdqa', 'B_wb_isobw', 'C_wb_full']

def parse_victim_line(path):
    if not os.path.exists(path):
        return None
    txt = open(path).read()
    m_ipc = re.search(r'ipc=([0-9.]+)', txt)
    m_l2 = re.search(r'l2_miss_rate=([0-9.]+)', txt)
    if not m_ipc:
        return None
    return float(m_ipc.group(1)), float(m_l2.group(1)) if m_l2 else float('nan')

def parse_bw(path):
    if not os.path.exists(path):
        return None
    txt = open(path).read()
    m = re.search(r'bw_gbps=([0-9.]+)', txt)
    return float(m.group(1)) if m else None

def mean_sd(v):
    if not v:
        return (float('nan'), float('nan'))
    if len(v) == 1:
        return (v[0], 0.0)
    return (statistics.mean(v), statistics.stdev(v))

def ci95(v):
    if len(v) < 2:
        return (float('nan'), float('nan'))
    m, s = mean_sd(v)
    h = 1.96 * s / math.sqrt(len(v))
    return (m - h, m + h)

def paired_diff(a_by_run, b_by_run):
    runs = sorted(set(a_by_run.keys()) & set(b_by_run.keys()))
    diffs = [a_by_run[r] - b_by_run[r] for r in runs]
    return runs, diffs

baseline = {}
for run in range(1, num_runs + 1):
    b = parse_victim_line(f"{outdir}/run{run}/baseline.txt")
    if b:
        baseline[run] = b[0]

rows = {}
for sc in scenarios:
    ipcs, l2s, bws, deltas = [], [], [], []
    ipc_by_run = {}
    for run in range(1, num_runs + 1):
        v = parse_victim_line(f"{outdir}/run{run}/{sc}_victim.txt")
        bw = parse_bw(f"{outdir}/run{run}/{sc}_aggressor.txt")
        if v:
            ipcs.append(v[0]); l2s.append(v[1])
            ipc_by_run[run] = v[0]
            if run in baseline:
                deltas.append(v[0] - baseline[run])
        if bw is not None:
            bws.append(bw)
    rows[sc] = dict(ipc=ipcs, l2=l2s, bw=bws, delta=deltas, ipc_by_run=ipc_by_run)

print("\n=================================================================")
print(f"Table 1 summary (n={num_runs}, topology=$TOPOLOGY)")
print("=================================================================")
print(f"Target WC BW: {wc_target:.3f} GB/s, iso tolerance: +/-{bw_tol:.1f}%")
print("\n{:<14} {:>8} {:>18} {:>18} {:>20}".format("Scenario", "n", "BW mean±sd", "IPC mean±sd", "IPC delta vs base"))
print("-" * 86)
for sc in scenarios:
    bw_m, bw_s = mean_sd(rows[sc]['bw'])
    ip_m, ip_s = mean_sd(rows[sc]['ipc'])
    d_m, d_s = mean_sd(rows[sc]['delta'])
    print("{:<14} {:>8} {:>9.3f}±{:<8.3f} {:>9.4f}±{:<8.4f} {:>10.4f}±{:<9.4f}".format(
        sc, len(rows[sc]['ipc']), bw_m, bw_s, ip_m, ip_s, d_m, d_s
    ))

# CI and significance
for sc in scenarios:
    lo, hi = ci95(rows[sc]['ipc'])
    if not math.isnan(lo):
        print(f"95% CI IPC {sc}: [{lo:.4f}, {hi:.4f}]")

# Paired effect-size CIs using within-run differences.
runs_ba, diffs_ba = paired_diff(rows['B_wb_isobw']['ipc_by_run'], rows['A_wc_ntdqa']['ipc_by_run'])
runs_ca, diffs_ca = paired_diff(rows['C_wb_full']['ipc_by_run'], rows['A_wc_ntdqa']['ipc_by_run'])
if len(diffs_ba) >= 2:
    lo, hi = ci95(diffs_ba)
    print(f"Paired diff IPC (B - A): mean={statistics.mean(diffs_ba):+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}], n={len(diffs_ba)}")
if len(diffs_ca) >= 2:
    lo, hi = ci95(diffs_ca)
    print(f"Paired diff IPC (C - A): mean={statistics.mean(diffs_ca):+.4f}, 95% CI [{lo:+.4f}, {hi:+.4f}], n={len(diffs_ca)}")

# Iso-bandwidth sanity checks
for sc in ['A_wc_ntdqa', 'B_wb_isobw']:
    bws = rows[sc]['bw']
    if not bws:
        continue
    bad = 0
    for bw in bws:
        err = abs((bw - wc_target) / wc_target) * 100.0
        if err > bw_tol:
            bad += 1
    print(f"BW match check {sc}: {len(bws)-bad}/{len(bws)} runs within +/-{bw_tol:.1f}%")

try:
    from scipy import stats
    def welch(a, b):
        if len(a) < 2 or len(b) < 2:
            return float('nan')
        return stats.ttest_ind(a, b, equal_var=False).pvalue

    def paired(a_by_run, b_by_run):
        runs = sorted(set(a_by_run.keys()) & set(b_by_run.keys()))
        if len(runs) < 2:
            return float('nan'), float('nan'), 0
        da = [a_by_run[r] for r in runs]
        db = [b_by_run[r] for r in runs]
        diffs = [x - y for x, y in zip(da, db)]
        return stats.ttest_rel(da, db).pvalue, statistics.mean(diffs), len(runs)

    p_ba_paired, d_ba, n_ba = paired(rows['B_wb_isobw']['ipc_by_run'], rows['A_wc_ntdqa']['ipc_by_run'])
    p_ca_paired, d_ca, n_ca = paired(rows['C_wb_full']['ipc_by_run'], rows['A_wc_ntdqa']['ipc_by_run'])
    print(f"Primary paired p(B_wb_isobw vs A_wc_ntdqa): {p_ba_paired:.6f}  (n={n_ba}, mean_diff={d_ba:+.4f})")
    print(f"Primary paired p(C_wb_full  vs A_wc_ntdqa): {p_ca_paired:.6f}  (n={n_ca}, mean_diff={d_ca:+.4f})")

    # Compact per-run paired difference log for auditability.
    if runs_ba:
        ba_items = ", ".join(f"r{r}:{d:+.4f}" for r, d in zip(runs_ba, diffs_ba))
        print(f"Per-run IPC diff (B-A): {ba_items}")
    if runs_ca:
        ca_items = ", ".join(f"r{r}:{d:+.4f}" for r, d in zip(runs_ca, diffs_ca))
        print(f"Per-run IPC diff (C-A): {ca_items}")

    p_ba = welch(rows['B_wb_isobw']['ipc'], rows['A_wc_ntdqa']['ipc'])
    p_ca = welch(rows['C_wb_full']['ipc'], rows['A_wc_ntdqa']['ipc'])
    print(f"Secondary Welch p(B_wb_isobw vs A_wc_ntdqa): {p_ba:.6f}")
    print(f"Secondary Welch p(C_wb_full  vs A_wc_ntdqa): {p_ca:.6f}")
except Exception:
    print("Paired/Welch p-values unavailable: install python3-scipy for tests.")
PY

echo ""
echo "=== Experiment 2 complete — results in $OUTDIR ==="
