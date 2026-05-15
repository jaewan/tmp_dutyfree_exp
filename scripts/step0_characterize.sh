#!/usr/bin/env bash
# step0_characterize.sh — Run as root, save full output.
# Every downstream decision (L2 size, perf events, CCD topology) depends on this.

echo "=== CPU ==="
lscpu | grep -E "Model name|Socket|Core|Thread|L1d|L1i|L2|L3|NUMA|CPU\(s\):"
cat /proc/cpuinfo | grep "model name" | head -1

echo ""
echo "=== Cache Geometry ==="
# L2 size per core (CRITICAL for victim sizing)
lscpu | grep "L2 cache"
getconf LEVEL2_CACHE_SIZE 2>/dev/null || echo "(getconf unavailable)"

echo ""
echo "=== CCD / Core Topology ==="
# Shows which cores share L2, L3
lscpu -e=CPU,CORE,SOCKET,NODE,L1d,L2,L3 | head -50
echo "... (first 50 cores)"

echo ""
echo "=== NUMA ==="
numactl --hardware

echo ""
echo "=== CXL Physical Range ==="
# Method 1: memblock
echo "-- memblock entries for node 2 --"
ls /sys/devices/system/node/node2/memblock/ 2>/dev/null | head -10
BLOCK_SZ=$(cat /sys/devices/system/memory/block_size_bytes 2>/dev/null)
echo "block_size_bytes=$BLOCK_SZ"

# Method 2: iomem
echo "-- /proc/iomem CXL ranges --"
cat /proc/iomem | grep -iE "system ram|cxl|hmem" | tail -20

echo ""
echo "=== CXL Devices ==="
ls -la /sys/bus/cxl/devices/ 2>/dev/null
daxctl list -u 2>/dev/null || echo "(daxctl not available)"

echo ""
echo "=== Available Perf Events (L2/L3) ==="
perf list 2>/dev/null | grep -iE "l2|l3|cache" | grep -v "Software" | head -30
echo ""
echo "=== AMD Uncore PMUs ==="
perf list 2>/dev/null | grep -iE "amd_l3|amd_df" | head -20

echo ""
echo "=== Boost / Frequency ==="
cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null && echo " (AMD boost)"
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor
cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq
