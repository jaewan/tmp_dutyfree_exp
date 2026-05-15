#!/usr/bin/env bash
#
# exp3_cat.sh — §3 "Cache Partitioning (CAT)": Demonstrate that Intel CAT
#               isolates L3 data array but NOT the Snoop Filter.
#
# Even with strict L3 partitioning, WB aggressors still degrade victim IPC
# because SF is shared and not partitioned by CAT.
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
OUTDIR="${OUTDIR:-./results/exp3}"
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

RESCTRL="/sys/fs/resctrl"

if ! mountpoint -q "$RESCTRL" 2>/dev/null; then
    echo "ERROR: resctrl not mounted. Run setup.sh first."
    exit 1
fi

# Read L3 CBM mask to determine number of ways
L3_MASK=$(cat "$RESCTRL/info/L3/cbm_mask")
echo "L3 CBM mask: $L3_MASK"
# Example: 0xfffff = 20 ways. Split 10/10.
# We'll give victim the upper half, aggressor the lower half.
TOTAL_BITS=$(echo "obase=2;ibase=16;$(echo $L3_MASK | tr '[:lower:]' '[:upper:]' | sed 's/0x//g')" \
             | bc | tr -cd '1' | wc -c)
HALF=$((TOTAL_BITS / 2))
AGG_MASK=$(printf "0x%x" $(( (1 << HALF) - 1 )))
VIC_MASK=$(printf "0x%x" $(( ((1 << HALF) - 1) << HALF )))

echo "Aggressor L3 mask: $AGG_MASK  ($HALF ways)"
echo "Victim    L3 mask: $VIC_MASK  ($HALF ways)"
echo ""

cleanup_resctrl() {
    rmdir "$RESCTRL/exp_agg"    2>/dev/null || true
    rmdir "$RESCTRL/exp_victim" 2>/dev/null || true
}
trap cleanup_resctrl EXIT

run_cat_scenario() {
    local NAME="$1"
    local USE_CAT="$2"
    local OUTPFX="$OUTDIR/$NAME"

    echo "=== Scenario: $NAME (CAT=$USE_CAT) ==="
    cleanup_resctrl

    if [ "$USE_CAT" = "yes" ]; then
        mkdir -p "$RESCTRL/exp_agg"
        mkdir -p "$RESCTRL/exp_victim"

        # Set schemata  — adjust the socket id (0) if needed
        echo "L3:0=$AGG_MASK" > "$RESCTRL/exp_agg/schemata"
        echo "L3:0=$VIC_MASK" > "$RESCTRL/exp_victim/schemata"
        # Assign CPUs to groups
        echo "$VICTIM_CORE" > "$RESCTRL/exp_victim/cpus_list"
        echo "$AGG_CORES" > "$RESCTRL/exp_agg/cpus_list"
        echo "  CAT schemata and CPU assignments configured"
    fi

    # Baseline
    echo "  [baseline] ..."
    "$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
        -d $((WARMUP + MEASURE)) -W $WARMUP \
        > "${OUTPFX}_baseline.txt" 2>&1 &
    BPID=$!
    wait $BPID || true

    # Stressed
    "$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
        -d $((WARMUP + MEASURE + 5)) -W $WARMUP \
        > "${OUTPFX}_victim.txt" 2>&1 &
    VPID=$!

    sleep $WARMUP

    "$BINDIR/aggressor" -m wb_load -t $AGG_THREADS -c "$AGG_CORES" \
        -s $PER_MB -d $((MEASURE + 3)) \
        > "${OUTPFX}_aggressor.txt" 2>&1 &
    APID=$!

    sleep 1
    perf stat -p $VPID -e cycles,instructions,r7064,r0864 \
        -o "${OUTPFX}_perf.txt" sleep $((MEASURE - 2)) &
    wait $! 2>/dev/null || true

    kill $APID 2>/dev/null; wait $APID 2>/dev/null || true
    kill $VPID 2>/dev/null; wait $VPID 2>/dev/null || true

    echo "  --- $NAME Results ---"
    grep "^VICTIM" "${OUTPFX}_baseline.txt" 2>/dev/null || true
    grep "^VICTIM" "${OUTPFX}_victim.txt"   2>/dev/null || true
    grep "^RESULT" "${OUTPFX}_aggressor.txt" 2>/dev/null || true
    echo ""
}

# -------------------------------------------------------------------
#  A: WB aggressor, NO CAT
# -------------------------------------------------------------------
run_cat_scenario "no_cat" "no"

# -------------------------------------------------------------------
#  B: WB aggressor, with CAT (strict L3 partition)
# -------------------------------------------------------------------
run_cat_scenario "with_cat" "yes"

# -------------------------------------------------------------------
#  Summary
# -------------------------------------------------------------------
echo "================================================================"
echo " CAT Experiment Summary"
echo "================================================================"
echo ""
for NAME in no_cat with_cat; do
    BL_IPC=$(grep "^VICTIM" "$OUTDIR/${NAME}_baseline.txt" 2>/dev/null \
             | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' \
             | cut -d= -f2 || echo "?")
    ST_IPC=$(grep "^VICTIM" "$OUTDIR/${NAME}_victim.txt" 2>/dev/null \
             | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' \
             | cut -d= -f2 || echo "?")
    ST_L2=$(grep "^VICTIM" "$OUTDIR/${NAME}_victim.txt" 2>/dev/null \
            | awk '{for(i=1;i<=NF;i++) if($i~/l2_miss_rate/) print $i}' \
            | cut -d= -f2 || echo "?")
    if [ "$BL_IPC" != "?" ] && [ "$ST_IPC" != "?" ]; then
        DELTA=$(python3 -c "print(f'{100*(1 - $ST_IPC/$BL_IPC):.1f}')" 2>/dev/null || echo "?")
    else
        DELTA="?"
    fi
    printf "%-12s  baseline_IPC=%-8s  stressed_IPC=%-8s  delta=-%s%%  L2miss=%s%%\n" \
           "$NAME" "$BL_IPC" "$ST_IPC" "$DELTA" "$ST_L2"
done

echo ""
echo "=== Experiment 3 complete — results in $OUTDIR ==="
