#!/usr/bin/env bash
# step1_offline_cxl.sh — Offline a portion of CXL memory for UC/WC experiments.
#
# IMPORTANT: On x86, offlining memory removes it from the buddy allocator but
# does NOT remove it from the kernel's linear/direct map.  remap_pfn_range()
# with pgprot_noncached() will therefore create PAT-conflicting cache types.
#
# The SAFE approach for UC/WC is to convert the CXL region to device-dax mode
# (see below).  This script provides the offline fallback for systems where
# daxctl is unavailable or the CXL region was auto-onlined as system-ram.
#
# Run as root.
set -euo pipefail

CXL_NODE=${CXL_NUMA_NODE:-2}

# ---- Parse block_size_bytes (sysfs outputs hex WITHOUT 0x prefix) ----
RAW_BLOCK_SZ=$(cat /sys/devices/system/memory/block_size_bytes)
BLOCK_SZ=$((16#${RAW_BLOCK_SZ}))
echo "Memory block size: 0x${RAW_BLOCK_SZ} = ${BLOCK_SZ} bytes ($((BLOCK_SZ/1024/1024)) MB)"

# ---- Find memory blocks on the CXL node ----
# Some kernels put them in .../nodeN/memblock/memoryX, others directly under nodeN/
NODE_DIR="/sys/devices/system/node/node${CXL_NODE}"
BLOCKS=()
for mb in "$NODE_DIR"/memory[0-9]* "$NODE_DIR"/memblock/memory[0-9]*; do
    [ -d "$mb" ] || continue
    BLK=$(basename "$mb")
    STATE=$(cat "$mb/state" 2>/dev/null || echo "unknown")
    BLOCKS+=("$BLK:$STATE:$mb")
done

if [ ${#BLOCKS[@]} -eq 0 ]; then
    echo "ERROR: No memory blocks found for node $CXL_NODE"
    echo "  Checked: $NODE_DIR/memory* and $NODE_DIR/memblock/memory*"
    echo "  Is the CXL memory online as system-ram on node $CXL_NODE?"
    exit 1
fi

echo "Found ${#BLOCKS[@]} memory blocks on node $CXL_NODE"

# ---- Determine how many to offline ----
OFFLINE_GB=${CXL_OFFLINE_GB:-8}
OFFLINE_BYTES=$((OFFLINE_GB * 1024 * 1024 * 1024))
N_OFFLINE=$((OFFLINE_BYTES / BLOCK_SZ))
if [ "$N_OFFLINE" -lt 1 ]; then N_OFFLINE=1; fi
if [ "$N_OFFLINE" -gt "${#BLOCKS[@]}" ]; then
    echo "WARNING: Requested $N_OFFLINE blocks but only ${#BLOCKS[@]} available."
    echo "  Will offline ALL blocks (entire CXL node). WB mode won't work."
    N_OFFLINE=${#BLOCKS[@]}
fi
echo "Will offline last $N_OFFLINE blocks ($((N_OFFLINE * BLOCK_SZ / 1024 / 1024)) MB)"

TOTAL=${#BLOCKS[@]}
START=$((TOTAL - N_OFFLINE))
OFFLINED_PHYS_START=""
OFFLINED_PHYS_END=""
FAIL_COUNT=0

for i in $(seq $START $((TOTAL-1))); do
    entry="${BLOCKS[$i]}"
    BLK=$(echo "$entry" | cut -d: -f1)
    MB_PATH=$(echo "$entry" | cut -d: -f3-)

    # phys_index is hex WITHOUT 0x prefix (e.g. "00000308"); block name fallback is decimal
    RAW_IDX=$(cat "$MB_PATH/phys_index" 2>/dev/null || echo "")
    if [ -n "$RAW_IDX" ]; then
        IDX=$((16#${RAW_IDX}))
        IDX_DISP="0x${RAW_IDX}"
    else
        IDX=$(echo "$BLK" | grep -oE '[0-9]+')
        IDX_DISP=$(printf "0x%x" "$IDX")
    fi
    PHYS=$((IDX * BLOCK_SZ))
    PHYS_HEX=$(printf "0x%x" $PHYS)

    # Offline via the canonical sysfs path
    SYS_MEM="/sys/devices/system/memory/${BLK}"
    echo -n "  Offlining $BLK (phys=$PHYS_HEX, idx=$IDX_DISP) ... "
    OFF_SUCCESS=0
    if [ -f "$SYS_MEM/state" ]; then
        CUR_STATE=$(cat "$SYS_MEM/state")
        if [ "$CUR_STATE" = "offline" ]; then
            echo "already offline"
            OFF_SUCCESS=1
        elif echo offline > "$SYS_MEM/state" 2>/dev/null; then
            echo "OK"
            OFF_SUCCESS=1
        else
            echo "FAILED"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    else
        echo "FAILED (no $SYS_MEM/state)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    if [ "$OFF_SUCCESS" -eq 1 ]; then
        [ -z "$OFFLINED_PHYS_START" ] && OFFLINED_PHYS_START=$PHYS_HEX
        OFFLINED_PHYS_END=$(printf "0x%x" $(( (IDX+1) * BLOCK_SZ )))
    fi
done

if [ "$FAIL_COUNT" -gt 0 ]; then
    echo ""
    echo "WARNING: $FAIL_COUNT block(s) failed to offline."
    echo "  Pages may still be in the kernel direct map (WB)."
    echo "  UC/WC mappings will PAT-alias → silently become WB."
fi

if [ -z "$OFFLINED_PHYS_START" ] || [ -z "$OFFLINED_PHYS_END" ]; then
    echo "ERROR: Could not determine offlined physical range."
    exit 1
fi

OFFLINED_SIZE=$(printf "0x%x" $((OFFLINED_PHYS_END - OFFLINED_PHYS_START)))
echo ""
echo "Offlined range:"
echo "  CXL_OFFLINE_BASE=$OFFLINED_PHYS_START"
echo "  CXL_OFFLINE_SIZE=$OFFLINED_SIZE"
echo ""
echo "Remaining online CXL memory:"
numactl --hardware 2>/dev/null | grep "node $CXL_NODE" || true

echo "CXL_OFFLINE_BASE=$OFFLINED_PHYS_START" > /tmp/cxl_offline_range.env
echo "CXL_OFFLINE_SIZE=$OFFLINED_SIZE" >> /tmp/cxl_offline_range.env
echo ""
echo "Saved to /tmp/cxl_offline_range.env"
echo ""
echo "=== IMPORTANT ==="
echo "Offlining alone does NOT remove pages from the kernel direct map."
echo "remap_pfn_range with UC/WC will PAT-conflict with the WB direct map."
echo "Verify after loading module: cat /sys/kernel/debug/x86/pat_memtype_list"
echo "If UC bandwidth ≈ WB bandwidth, the mapping is silently WB (PAT aliasing)."
