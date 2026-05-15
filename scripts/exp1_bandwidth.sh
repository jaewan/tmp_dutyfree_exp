#!/usr/bin/env bash
#
# exp1_bandwidth.sh -- Bandwidth characterization with confound controls.
#
# Improvements:
#  - Uses one logical CPU per physical core (avoids SMT confounds)
#  - Supports matched working-set comparisons across WB/WC/UC
#  - Emits explicit warnings when WC device is too small for fair comparison
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
OUTDIR="${OUTDIR:-./results/exp1}"
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

DURATION="${DURATION_SEC:-15}"
PER_MB="${PER_MB:-256}"
AGG_NODE="${AGG_NUMA_NODE:-1}"
TIMEOUT_S="${TIMEOUT_S:-120}"
MAX_THREADS="${MAX_THREADS:-32}"
MATCH_WORKING_SET="${MATCH_WORKING_SET:-1}"
THREAD_POINTS="${THREAD_POINTS:-1 2 4 8 12 16 20 24 28 32}"
AGG_CORE_LIST="${AGG_CORE_LIST:-}"   # optional explicit comma-separated core list

# One logical CPU per physical core on AGG_NODE.
mapfile -t ALL_CORES < <(python3 - <<PY
import subprocess
agg_node = int("$AGG_NODE")
max_threads = int("$MAX_THREADS")
agg_core_list = "$AGG_CORE_LIST".strip()
out = subprocess.check_output(['lscpu', '-e'], text=True)
rows = [ln for ln in out.splitlines()[1:] if ln.strip()]
phys = {}
for ln in rows:
    p = ln.split()
    if len(p) < 4:
        continue
    cpu,node,socket,core = map(int, p[:4])
    if node != agg_node:
        continue
    key = (socket, core)
    if key not in phys or cpu < phys[key]:
        phys[key] = cpu
cpus = sorted(phys.values())
if agg_core_list:
    req = []
    for tok in agg_core_list.split(','):
        tok = tok.strip()
        if tok:
            req.append(int(tok))
    allowed = set(cpus)
    bad = [c for c in req if c not in allowed]
    if bad:
        raise SystemExit("ERROR: AGG_CORE_LIST has invalid/non-physical/non-node cores: " + ",".join(map(str, bad)))
    cpus = req[:max_threads]
else:
    cpus = cpus[:max_threads]
for c in cpus:
    print(c)
PY
)

if [ ${#ALL_CORES[@]} -eq 0 ]; then
    echo "ERROR: No physical cores found on NUMA node $AGG_NODE"
    exit 1
fi

echo "=============================================================="
echo " Experiment 1: CXL Bandwidth Characterization"
echo "=============================================================="
echo "Aggressor node=$AGG_NODE cores=${#ALL_CORES[@]} (physical), duration=${DURATION}s"
echo "Aggressor core list: $(IFS=,; echo "${ALL_CORES[*]}")"
echo "per-thread working set target=${PER_MB}MB"

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
UC_DEV_MB="$WC_DEV_MB"

echo "WC/UC device size estimate: ${WC_DEV_MB}MB each"

run_aggressor() {
    local label="$1"; shift
    local outf="$1"; shift
    echo -n "  $label ... "
    if timeout "${TIMEOUT_S}s" "$BINDIR/aggressor" "$@" > "$outf" 2>&1; then
        local bw
        bw=$(grep '^RESULT' "$outf" | grep -oP 'bw_gbps=\K[0-9.]+' || echo "N/A")
        echo "$bw GB/s"
    else
        local ec=$?
        if [ "$ec" -eq 124 ]; then
            echo "TIMEOUT"
        else
            echo "FAILED (exit $ec)"
        fi
        tail -3 "$outf" 2>/dev/null || true
    fi
}

build_corelist() {
    local nthrd="$1"
    echo "${ALL_CORES[@]:0:$nthrd}" | tr ' ' ','
}

# Filter thread points to available cores.
VALID_THREADS=()
for n in $THREAD_POINTS; do
    if [ "$n" -ge 1 ] && [ "$n" -le "${#ALL_CORES[@]}" ]; then
        VALID_THREADS+=("$n")
    fi
done
if [ ${#VALID_THREADS[@]} -eq 0 ]; then
    echo "ERROR: No valid thread counts in THREAD_POINTS='$THREAD_POINTS'"
    exit 1
fi

echo "Thread points: ${VALID_THREADS[*]}"
echo ""

# 1A. Single-thread check
echo "--- 1A: Single-thread bandwidth ---"
for mode in wb_load wb_ntdqa wb_prefetchnta; do
    run_aggressor "mode=$mode" "$OUTDIR/single_${mode}.txt" \
        -m "$mode" -t 1 -c "${ALL_CORES[0]}" -s "$PER_MB" -d "$DURATION"
done
if [ -c /dev/cxl_wc ] && [ "$WC_DEV_MB" -ge "$PER_MB" ]; then
    run_aggressor "mode=wc_ntdqa" "$OUTDIR/single_wc_ntdqa.txt" \
        -m wc_ntdqa -t 1 -c "${ALL_CORES[0]}" -s "$PER_MB" -d "$DURATION"
else
    echo "  mode=wc_ntdqa ... SKIPPED (no /dev/cxl_wc or insufficient WC size)"
fi
if [ -c /dev/cxl_uc ] && [ "$UC_DEV_MB" -ge "$PER_MB" ]; then
    run_aggressor "mode=uc_load" "$OUTDIR/single_uc_load.txt" \
        -m uc_load -t 1 -c "${ALL_CORES[0]}" -s "$PER_MB" -d "$DURATION"
else
    echo "  mode=uc_load ... SKIPPED (no /dev/cxl_uc or insufficient UC size)"
fi

echo ""
echo "--- 1B: WB scaling ---"
for nthrd in "${VALID_THREADS[@]}"; do
    corelist=$(build_corelist "$nthrd")
    run_aggressor "wb threads=$nthrd (${PER_MB}MB/t)" "$OUTDIR/scale_wb_${nthrd}t.txt" \
        -m wb_load -t "$nthrd" -c "$corelist" -s "$PER_MB" -d "$DURATION"
done

echo ""
echo "--- 1C: WB_NTDQA scaling ---"
for nthrd in "${VALID_THREADS[@]}"; do
    corelist=$(build_corelist "$nthrd")
    run_aggressor "wb_ntdqa threads=$nthrd (${PER_MB}MB/t)" "$OUTDIR/scale_wb_ntdqa_${nthrd}t.txt" \
        -m wb_ntdqa -t "$nthrd" -c "$corelist" -s "$PER_MB" -d "$DURATION"
done

echo ""
echo "--- 1D: WC scaling ---"
if [ -c /dev/cxl_wc ]; then
    for nthrd in "${VALID_THREADS[@]}"; do
        corelist=$(build_corelist "$nthrd")
        if [ "$MATCH_WORKING_SET" = "1" ]; then
            need=$((nthrd * PER_MB))
            if [ "$WC_DEV_MB" -lt "$need" ]; then
                echo "  wc threads=$nthrd ... SKIPPED (need ${need}MB, have ${WC_DEV_MB}MB)"
                continue
            fi
            wc_per_mb="$PER_MB"
        else
            wc_per_mb=$((WC_DEV_MB / nthrd))
            [ "$wc_per_mb" -lt 8 ] && { echo "  wc threads=$nthrd ... SKIPPED (<8MB/t)"; continue; }
        fi
        run_aggressor "wc threads=$nthrd (${wc_per_mb}MB/t)" "$OUTDIR/scale_wc_ntdqa_${nthrd}t.txt" \
            -m wc_ntdqa -t "$nthrd" -c "$corelist" -s "$wc_per_mb" -d "$DURATION"
    done
else
    echo "  SKIPPED (no /dev/cxl_wc)"
fi

echo ""
echo "--- 1E: UC scaling ---"
if [ -c /dev/cxl_uc ]; then
    for nthrd in "${VALID_THREADS[@]}"; do
        corelist=$(build_corelist "$nthrd")
        if [ "$MATCH_WORKING_SET" = "1" ]; then
            need=$((nthrd * PER_MB))
            if [ "$UC_DEV_MB" -lt "$need" ]; then
                echo "  uc threads=$nthrd ... SKIPPED (need ${need}MB, have ${UC_DEV_MB}MB)"
                continue
            fi
            uc_per_mb="$PER_MB"
        else
            uc_per_mb=$((UC_DEV_MB / nthrd))
            [ "$uc_per_mb" -lt 8 ] && { echo "  uc threads=$nthrd ... SKIPPED (<8MB/t)"; continue; }
        fi
        run_aggressor "uc threads=$nthrd (${uc_per_mb}MB/t)" "$OUTDIR/scale_uc_${nthrd}t.txt" \
            -m uc_load -t "$nthrd" -c "$corelist" -s "$uc_per_mb" -d "$DURATION"
    done
else
    echo "  SKIPPED (no /dev/cxl_uc)"
fi

echo ""
echo "--- 1F: Matched-size cross-mode checkpoints ---"
MATCHED_THREADS=()
for nthrd in "${VALID_THREADS[@]}"; do
    need=$((nthrd * PER_MB))
    if [ "$WC_DEV_MB" -ge "$need" ] && [ "$UC_DEV_MB" -ge "$need" ]; then
        MATCHED_THREADS+=("$nthrd")
    fi
done
if [ ${#MATCHED_THREADS[@]} -eq 0 ]; then
    echo "  No thread counts support fair WB/WC/UC matching at ${PER_MB}MB/thread."
    echo "  Increase offlined CXL size or reduce PER_MB."
else
    for nthrd in "${MATCHED_THREADS[@]}"; do
        corelist=$(build_corelist "$nthrd")
        run_aggressor "fair wb t=$nthrd" "$OUTDIR/fair_wb_${nthrd}t.txt" \
            -m wb_load -t "$nthrd" -c "$corelist" -s "$PER_MB" -d "$DURATION"
        [ -c /dev/cxl_wc ] && run_aggressor "fair wc t=$nthrd" "$OUTDIR/fair_wc_ntdqa_${nthrd}t.txt" \
            -m wc_ntdqa -t "$nthrd" -c "$corelist" -s "$PER_MB" -d "$DURATION"
        [ -c /dev/cxl_uc ] && run_aggressor "fair uc t=$nthrd" "$OUTDIR/fair_uc_${nthrd}t.txt" \
            -m uc_load -t "$nthrd" -c "$corelist" -s "$PER_MB" -d "$DURATION"
    done
fi

echo ""
echo "=== Experiment 1 complete -- results in $OUTDIR ==="
