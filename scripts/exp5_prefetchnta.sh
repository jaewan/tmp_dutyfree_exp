#!/usr/bin/env bash
#
# exp5_prefetchnta.sh — §3 "Software Prefetching": Show PREFETCHNTA
#                        on WB still causes SF pollution identical to
#                        regular WB loads.
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
OUTDIR="${OUTDIR:-./results/exp5}"
rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"

VICTIM_CORE=0
VICTIM_WS_KB=256        # 3x256=768KB (75% of 1MB L2)
WARMUP=5
MEASURE=15
PER_MB=256

AGG_NODE=${AGG_NUMA_NODE:-1}
AGG_ALL=($(lscpu -p=cpu,node 2>/dev/null | grep -v '^#' | awk -F, -v n="$AGG_NODE" '$2==n {print $1}' | head -16))
AGG_THREADS=${#AGG_ALL[@]}
AGG_CORES=$(IFS=,; echo "${AGG_ALL[*]}")

run_mode() {
    local MODE="$1"
    local NAME="$2"

    echo "=== $NAME: mode=$MODE ==="

    # Baseline
    "$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
        -d $((WARMUP + MEASURE)) -W $WARMUP \
        > "$OUTDIR/${NAME}_baseline.txt" 2>&1

    # Stressed
    "$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
        -d $((WARMUP + MEASURE + 5)) -W $WARMUP \
        > "$OUTDIR/${NAME}_victim.txt" 2>&1 &
    VPID=$!
    sleep $WARMUP

    "$BINDIR/aggressor" -m "$MODE" -t $AGG_THREADS -c "$AGG_CORES" \
        -s $PER_MB -d $((MEASURE + 3)) \
        > "$OUTDIR/${NAME}_aggressor.txt" 2>&1 &
    APID=$!

    sleep $((MEASURE + 2))
    kill $APID 2>/dev/null; wait $APID 2>/dev/null || true
    kill $VPID 2>/dev/null; wait $VPID 2>/dev/null || true

    BL=$(grep "^VICTIM" "$OUTDIR/${NAME}_baseline.txt" | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' | cut -d= -f2)
    ST=$(grep "^VICTIM" "$OUTDIR/${NAME}_victim.txt"   | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' | cut -d= -f2)
    BW=$(grep "^RESULT" "$OUTDIR/${NAME}_aggressor.txt" | awk '{for(i=1;i<=NF;i++) if($i~/bw_gbps/) print $i}' | cut -d= -f2)
    echo "  BW=${BW} GB/s   baseline_IPC=${BL}   stressed_IPC=${ST}"
    echo ""
}

run_mode "wb_load"        "wb_standard"
run_mode "wb_prefetchnta" "wb_prefetchnta"
run_mode "wc_ntdqa"       "wc_ntdqa"

echo "=== Summary ==="
echo "If wb_prefetchnta IPC delta ≈ wb_standard delta >> wc_ntdqa delta,"
echo "then PREFETCHNTA provides NO directory relief on WB memory."
echo ""
echo "=== Experiment 5 complete — results in $OUTDIR ==="
