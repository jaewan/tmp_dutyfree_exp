#!/usr/bin/env bash
#
# detect_cxl.sh — Auto-detect CXL NUMA node physical address range.
#
# Outputs:  CXL_PHYS_BASE  CXL_PHYS_SIZE  (hex)
#
set -euo pipefail

CXL_NODE=${CXL_NUMA_NODE:-2}

echo "=== CXL Memory Detection (NUMA node $CXL_NODE) ==="
echo ""

# Method 1: /sys/devices/system/node/nodeX/memblock/
MEMBLOCK_DIR="/sys/devices/system/node/node${CXL_NODE}/memblock"
if [ -d "$MEMBLOCK_DIR" ]; then
    echo "[memblock] scanning $MEMBLOCK_DIR ..."
    BLOCK_SZ=$(cat /sys/devices/system/memory/block_size_bytes 2>/dev/null || echo "0x8000000")
    echo "  Memory block size: $BLOCK_SZ bytes"
    FIRST=""
    LAST=""
    for mb in "$MEMBLOCK_DIR"/memory*; do
        IDX=$(cat "$mb/phys_index" 2>/dev/null || echo "")
        if [ -n "$IDX" ]; then
            ADDR=$(printf "0x%x" $((0x$IDX * 0x$BLOCK_SZ)))
            [ -z "$FIRST" ] && FIRST=$ADDR
            LAST=$ADDR
        fi
    done
    if [ -n "$FIRST" ] && [ -n "$LAST" ]; then
        END=$(printf "0x%x" $(( $LAST + 0x$BLOCK_SZ )))
        SIZE=$(printf "0x%x" $(( $END - $FIRST )))
        echo "  Base = $FIRST"
        echo "  End  = $END"
        echo "  Size = $SIZE  ($(( $SIZE / 1024 / 1024 )) MB)"
        echo ""
        echo "export CXL_PHYS_BASE=$FIRST"
        echo "export CXL_PHYS_SIZE=$SIZE"
        exit 0
    fi
fi

# Method 2: /proc/iomem
echo "[iomem] scanning /proc/iomem ..."
# On many CXL systems the range appears labeled "System RAM (CXL)" or similar.
# Fallback: find the highest "System RAM" region.
IOMEM_LINE=$(grep -i "cxl\|hmem\|soft reserved" /proc/iomem 2>/dev/null \
             | head -1 || true)
if [ -z "$IOMEM_LINE" ]; then
    # Heuristic: the last large System RAM region is likely CXL
    IOMEM_LINE=$(grep "System RAM" /proc/iomem | tail -1)
fi

if [ -n "$IOMEM_LINE" ]; then
    RANGE=$(echo "$IOMEM_LINE" | awk '{print $1}')
    START="0x${RANGE%-*}"
    END="0x${RANGE#*-}"
    SIZE=$(printf "0x%x" $(( $END - $START + 1 )))
    echo "  iomem: $IOMEM_LINE"
    echo "  Base = $START"
    echo "  Size = $SIZE  ($(( ($END - $START + 1) / 1024 / 1024 )) MB)"
    echo ""
    echo "export CXL_PHYS_BASE=$START"
    echo "export CXL_PHYS_SIZE=$SIZE"
    exit 0
fi

# Method 3: numactl
echo "[numactl] NUMA hardware info:"
numactl --hardware 2>/dev/null | grep -A2 "node $CXL_NODE"
echo ""
echo "ERROR: could not auto-detect CXL physical range."
echo "Please find it manually via:  cat /proc/iomem | grep -i cxl"
echo "Then:  export CXL_PHYS_BASE=0x...  CXL_PHYS_SIZE=0x..."
exit 1
