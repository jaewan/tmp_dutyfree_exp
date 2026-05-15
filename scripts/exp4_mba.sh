#!/usr/bin/env bash
#
# exp4_mba.sh — §3 "Bandwidth Throttling (MBA)": Sweep MBA delay values
#               to find the throttle level at which victim IPC recovers.
#
# Shows that MBA must reduce CXL BW below MOVNTDQA levels to be effective,
# because the problem is SF enrollment rate, not raw bus pressure.
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
OUTDIR="${OUTDIR:-./results/exp4}"
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

# Check MBA support
if [ ! -d "$RESCTRL/info/MB" ]; then
    echo "ERROR: MBA not supported on this platform."
    exit 1
fi

MBA_MIN=$(cat "$RESCTRL/info/MB/min_bandwidth" 2>/dev/null || echo 10)
MBA_GRAN=$(cat "$RESCTRL/info/MB/bandwidth_gran" 2>/dev/null || echo 10)
echo "MBA: min=$MBA_MIN%  granularity=$MBA_GRAN%"

cleanup_resctrl() {
    rmdir "$RESCTRL/exp_mba_agg" 2>/dev/null || true
}
trap cleanup_resctrl EXIT

# -------------------------------------------------------------------
#  Baseline (no aggressor)
# -------------------------------------------------------------------
echo "=== Baseline (no aggressor) ==="
"$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
    -d $((WARMUP + MEASURE)) -W $WARMUP \
    > "$OUTDIR/baseline.txt" 2>&1

BL_IPC=$(grep "^VICTIM" "$OUTDIR/baseline.txt" | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' | cut -d= -f2)
echo "  Baseline IPC: $BL_IPC"
echo ""

# -------------------------------------------------------------------
#  Sweep MBA from 100% (no throttle) down to minimum
# -------------------------------------------------------------------
echo "=== MBA Sweep ==="
echo ""
printf "%-8s  %12s  %10s  %10s  %10s\n" "MBA%" "Agg_BW_GBps" "Vict_IPC" "IPC_Delta%" "L2miss%"
echo "------------------------------------------------------------"

# MBA values: 100 (no throttle), 90, 80, ..., MBA_MIN
MBA_VALUES=$(seq 100 -$MBA_GRAN $MBA_MIN)

for MBA_PCT in $MBA_VALUES; do
    cleanup_resctrl
    mkdir -p "$RESCTRL/exp_mba_agg"

    # Set MBA throttle for aggressor group
    echo "MB:0=$MBA_PCT" > "$RESCTRL/exp_mba_agg/schemata" 2>/dev/null || {
        echo "  MBA=$MBA_PCT% : write failed, skipping"
        continue
    }
    # Assign CPUs to aggressor group
    echo "$AGG_CORES" > "$RESCTRL/exp_mba_agg/cpus_list"

    # Launch victim
    "$BINDIR/victim" -c $VICTIM_CORE -w $VICTIM_WS_KB \
        -d $((WARMUP + MEASURE + 5)) -W $WARMUP \
        > "$OUTDIR/mba_${MBA_PCT}_victim.txt" 2>&1 &
    VPID=$!

    sleep $WARMUP

    # Launch aggressor in MBA group
    "$BINDIR/aggressor" -m wb_load -t $AGG_THREADS -c "$AGG_CORES" \
        -s $PER_MB -d $((MEASURE + 3)) \
        > "$OUTDIR/mba_${MBA_PCT}_aggressor.txt" 2>&1 &
    APID=$!

    sleep 1
    perf stat -p $VPID -e cycles,instructions,r7064,r0864 \
        -o "$OUTDIR/mba_${MBA_PCT}_perf.txt" sleep $((MEASURE - 2)) &
    PPID=$!
    wait $PPID 2>/dev/null || true

    kill $APID 2>/dev/null; wait $APID 2>/dev/null || true
    kill $VPID 2>/dev/null; wait $VPID 2>/dev/null || true

    # Parse results
    AGG_BW=$(grep "^RESULT" "$OUTDIR/mba_${MBA_PCT}_aggressor.txt" 2>/dev/null \
             | awk '{for(i=1;i<=NF;i++) if($i~/bw_gbps/) print $i}' \
             | cut -d= -f2 || echo "N/A")
    V_IPC=$(grep "^VICTIM" "$OUTDIR/mba_${MBA_PCT}_victim.txt" 2>/dev/null \
            | awk '{for(i=1;i<=NF;i++) if($i~/ipc=/) print $i}' \
            | cut -d= -f2 || echo "N/A")
    V_L2M=$(grep "^VICTIM" "$OUTDIR/mba_${MBA_PCT}_victim.txt" 2>/dev/null \
            | awk '{for(i=1;i<=NF;i++) if($i~/l2_miss_rate/) print $i}' \
            | cut -d= -f2 || echo "N/A")

    DELTA="N/A"
    if [ "$V_IPC" != "N/A" ] && [ "$BL_IPC" != "N/A" ]; then
        DELTA=$(python3 -c "print(f'{100*(1 - $V_IPC/$BL_IPC):.1f}')" 2>/dev/null || echo "N/A")
    fi

    printf "%-8s  %12s  %10s  %10s  %10s\n" \
           "${MBA_PCT}%" "$AGG_BW" "$V_IPC" "-${DELTA}%" "$V_L2M"
done

echo ""
echo "=== Experiment 4 complete — results in $OUTDIR ==="
echo ""
echo "The MBA% at which IPC_Delta ≤ ~5% indicates the throttle level"
echo "required to 'escape' the trap — compare to WC+NTDQA aggregate BW"
echo "from Experiment 1."
