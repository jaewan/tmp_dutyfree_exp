#!/usr/bin/env bash
#
# safe_smoke_test.sh -- Incremental safety tests for each component.
#
# Run as root.  Tests each layer in isolation with tiny buffers, short
# durations, and hard timeouts so a failure cannot hang the server.
#
# Gate: each step must pass before proceeding to the next.
#
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
TIMEOUT_S=30
SMALL_MB=4
SHORT_DUR=3
CXL_NODE=${CXL_NUMA_NODE:-2}
TEST_CORE=1

PASS=0
FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "============================================="
echo " Safe Smoke Test ($(date))"
echo " Timeout per test: ${TIMEOUT_S}s"
echo "============================================="
echo ""

# -----------------------------------------------------------------
# TEST 1: System topology sanity
# -----------------------------------------------------------------
echo "--- TEST 1: System topology ---"

NCPUS=$(nproc)
echo "  CPUs: $NCPUS"
if [ "$NCPUS" -lt 4 ]; then
    fail "Need at least 4 CPUs"; exit 1
fi
pass "CPU count OK"

if numactl --hardware 2>/dev/null | grep -q "node $CXL_NODE"; then
    CXL_FREE=$(numactl --hardware 2>/dev/null | grep "node $CXL_NODE free" | awk '{print $4}')
    echo "  CXL node $CXL_NODE free: ${CXL_FREE} MB"
    if [ "${CXL_FREE:-0}" -gt 100 ]; then
        pass "CXL node has free memory"
    else
        fail "CXL node has < 100 MB free"
    fi
else
    fail "CXL NUMA node $CXL_NODE not found"
    exit 1
fi

L2_TOTAL=$(lscpu 2>/dev/null | grep "L2 cache" | grep -oP '[0-9]+' | head -1)
L2_INSTANCES=$(lscpu 2>/dev/null | grep "L2 cache" | grep -oP '\(([0-9]+)' | tr -d '(' || echo "")
if [ -n "$L2_INSTANCES" ] && [ "$L2_INSTANCES" -gt 0 ]; then
    # lscpu output is in MiB/GiB, but grep -oP '[0-9]+' might get the number
    # Let's use a more robust way to get L2 per core in KB
    L2_PER_CORE_KB=$(getconf LEVEL2_CACHE_SIZE 2>/dev/null)
    if [ -n "$L2_PER_CORE_KB" ]; then
        L2_PER_CORE_KB=$((L2_PER_CORE_KB / 1024))
    else
        L2_PER_CORE_KB=$((L2_TOTAL * 1024 / L2_INSTANCES))
    fi
    echo "  L2 per core: ${L2_PER_CORE_KB} KB"
    if [ "$L2_PER_CORE_KB" -eq 1024 ]; then
        pass "L2 = 1 MB/core (matches common.h)"
    else
        fail "L2 = ${L2_PER_CORE_KB} KB/core -- update L2_SIZE_BYTES in common.h!"
    fi
fi
echo ""

# -----------------------------------------------------------------
# TEST 2: WB allocation on CXL node (tiny)
# -----------------------------------------------------------------
echo "--- TEST 2: WB CXL allocation (${SMALL_MB} MB, ${SHORT_DUR}s) ---"

if timeout ${TIMEOUT_S}s "$BINDIR/aggressor" \
    -m wb_load -t 1 -c $TEST_CORE -s $SMALL_MB -d $SHORT_DUR \
    > /tmp/smoke_wb.txt 2>&1; then
    BW=$(grep "^RESULT" /tmp/smoke_wb.txt | grep -oP 'bw_gbps=\K[0-9.]+' || echo "0")
    echo "  WB bandwidth: $BW GB/s"
    pass "WB CXL read works"
else
    fail "WB aggressor timed out or crashed"
    tail -5 /tmp/smoke_wb.txt 2>/dev/null || true
fi
echo ""

# -----------------------------------------------------------------
# TEST 3: AMD perf events
# -----------------------------------------------------------------
echo "--- TEST 3: AMD perf events ---"

if timeout ${TIMEOUT_S}s perf stat -e r7064,r0864,cycles,instructions \
    "$BINDIR/aggressor" -m wb_load -t 1 -c $TEST_CORE -s $SMALL_MB -d 2 \
    > /tmp/smoke_perf.txt 2>&1; then
    if grep -q "not supported" /tmp/smoke_perf.txt; then
        fail "r7064/r0864 not supported -- wrong CPU or perf config"
    else
        pass "AMD L2 perf events accepted"
    fi
else
    fail "Perf stat timed out or failed"
fi
echo ""

# -----------------------------------------------------------------
# TEST 4: Victim workload (local DRAM)
# -----------------------------------------------------------------
echo "--- TEST 4: Victim workload (${SHORT_DUR}s) ---"

VICTIM_WS=256
if timeout ${TIMEOUT_S}s "$BINDIR/victim" \
    -c $TEST_CORE -w $VICTIM_WS -d $SHORT_DUR -W 1 \
    > /tmp/smoke_victim.txt 2>&1; then
    IPC=$(grep "^VICTIM" /tmp/smoke_victim.txt | grep -oP 'ipc=\K[0-9.]+' || echo "0")
    L2M=$(grep "^VICTIM" /tmp/smoke_victim.txt | grep -oP 'l2_miss_rate=\K[0-9.]+' || echo "?")
    echo "  Baseline IPC: $IPC  L2 miss rate: ${L2M}%"
    pass "Victim workload runs"
else
    fail "Victim timed out or crashed"
fi
echo ""

# -----------------------------------------------------------------
# TEST 5: Kernel module and device status
# -----------------------------------------------------------------
echo "--- TEST 5: Kernel module status ---"

if [ -c /dev/cxl_uc ] && [ -c /dev/cxl_wc ]; then
    echo "  /dev/cxl_uc and /dev/cxl_wc exist"
    pass "Module appears loaded"
else
    echo "  /dev/cxl_uc or /dev/cxl_wc missing"
    echo "  Module not loaded. UC/WC modes unavailable."
    echo "  WB-only experiments are still valid."
    pass "Module not loaded (safe default)"
fi
echo ""

# -----------------------------------------------------------------
# TEST 6: CXL memory offline status
# -----------------------------------------------------------------
echo "--- TEST 6: CXL memory offline status ---"

OFFLINE_COUNT=0
ONLINE_COUNT=0
for mb in /sys/devices/system/node/node${CXL_NODE}/memory[0-9]*; do
    [ -d "$mb" ] || continue
    STATE=$(cat "$mb/state" 2>/dev/null || echo "unknown")
    if [ "$STATE" = "offline" ]; then
        OFFLINE_COUNT=$((OFFLINE_COUNT + 1))
    else
        ONLINE_COUNT=$((ONLINE_COUNT + 1))
    fi
done
echo "  Node $CXL_NODE: $ONLINE_COUNT online, $OFFLINE_COUNT offline"
if [ "$OFFLINE_COUNT" -eq 0 ] && [ -c /dev/cxl_uc ]; then
    fail "Module loaded but NO blocks offline -- PAT aliasing!"
    echo "  UC/WC will silently be WB. Run step1_offline_cxl.sh first."
else
    pass "Offline status consistent with module state"
fi
echo ""

# -----------------------------------------------------------------
# TEST 7: Multi-thread WB (4 threads, small buffer)
# -----------------------------------------------------------------
echo "--- TEST 7: Multi-thread WB (4 threads, ${SMALL_MB} MB, ${SHORT_DUR}s) ---"

if timeout ${TIMEOUT_S}s "$BINDIR/aggressor" \
    -m wb_load -t 4 -c 1,2,3,4 -s $SMALL_MB -d $SHORT_DUR \
    > /tmp/smoke_wb_mt.txt 2>&1; then
    BW=$(grep "^RESULT" /tmp/smoke_wb_mt.txt | grep -oP 'bw_gbps=\K[0-9.]+' || echo "0")
    echo "  4-thread WB bandwidth: $BW GB/s"
    pass "Multi-thread WB works"
else
    fail "Multi-thread WB timed out or crashed"
fi
echo ""

# -----------------------------------------------------------------
# TEST 8: Victim + aggressor co-run (short)
# -----------------------------------------------------------------
echo "--- TEST 8: Victim + 4-thread WB aggressor co-run ---"

timeout ${TIMEOUT_S}s "$BINDIR/victim" \
    -c 0 -w 256 -d $((SHORT_DUR + 2)) -W 1 \
    > /tmp/smoke_corun_victim.txt 2>&1 &
VPID=$!

sleep 2
timeout ${TIMEOUT_S}s "$BINDIR/aggressor" \
    -m wb_load -t 4 -c 1,2,3,4 -s $SMALL_MB -d $SHORT_DUR \
    > /tmp/smoke_corun_agg.txt 2>&1 &
APID=$!

wait $APID 2>/dev/null || true
wait $VPID 2>/dev/null || true

if grep -q "^VICTIM" /tmp/smoke_corun_victim.txt 2>/dev/null; then
    IPC=$(grep "^VICTIM" /tmp/smoke_corun_victim.txt | grep -oP 'ipc=\K[0-9.]+' || echo "?")
    echo "  Victim IPC under aggressor: $IPC"
    pass "Co-run completed"
else
    fail "Co-run: victim produced no output"
fi
echo ""

# -----------------------------------------------------------------
# Summary
# -----------------------------------------------------------------
echo "============================================="
echo " Results: $PASS passed, $FAIL failed"
echo "============================================="

if [ "$FAIL" -gt 0 ]; then
    echo ""
    echo "FIX failures before running full experiments."
    echo "See /tmp/smoke_*.txt for detailed output."
    exit 1
fi
echo ""
echo "All tests passed. Safe to run full experiments (WB mode)."
echo ""
echo "For UC/WC: run bin/validate -c 1 and verify UC BW << WB BW."
echo "If UC BW = WB BW, mapping is broken (PAT aliasing)."
