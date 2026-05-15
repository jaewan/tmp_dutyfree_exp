#!/usr/bin/env bash
#
# setup.sh — Prepare the system for coherence-trap experiments (AMD or Intel).
#
# Run as root. For UC/WC modes, run step1_offline_cxl.sh first to offline
# CXL memory; this script will load cxl_memtype with the offlined range.
#
set -euo pipefail

echo "=== AMD/Intel System Setup for Coherence-Trap Experiments ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CXL_NODE=${CXL_NUMA_NODE:-2}
LOCAL_NODE=${LOCAL_NUMA_NODE:-0}

# ---- 1. Kernel tuning -----------------------------------------------
echo "[1/7] Kernel tuning ..."
echo -1    > /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true
echo 0     > /proc/sys/kernel/nmi_watchdog        2>/dev/null || true
echo 0     > /proc/sys/kernel/numa_balancing      2>/dev/null || true
echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
echo 2     > /sys/bus/event_source/devices/cpu/rdpmc 2>/dev/null || true

# ---- 2. CPU governor -------------------------------------------------
echo "[2/7] Setting performance governor ..."
for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance > "$f" 2>/dev/null || true
done

# ---- 3. Disable turbo boost (AMD or Intel) ---------------------------
echo "[3/7] Disabling turbo boost ..."
if [ -f /sys/devices/system/cpu/cpufreq/boost ]; then
    echo 0 > /sys/devices/system/cpu/cpufreq/boost
    echo "  AMD boost disabled"
elif [ -f /sys/devices/system/cpu/intel_pstate/no_turbo ]; then
    echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo
    echo "  Intel turbo disabled"
fi

# ---- 4. Offline CXL memory and load kernel module --------------------
# MUST happen BEFORE hugepage allocation. If hugepages land on the blocks
# we intend to offline, offlineing will fail (pages are pinned).
echo "[4/7] CXL offline range + cxl_memtype module ..."
if [ -f /tmp/cxl_offline_range.env ]; then
    source /tmp/cxl_offline_range.env
    echo "  Using /tmp/cxl_offline_range.env (run scripts/step1_offline_cxl.sh first if not done)"
else
    echo "  Running step1_offline_cxl.sh to offline 8 GB CXL ..."
    bash "$SCRIPT_DIR/step1_offline_cxl.sh" || true
    if [ -f /tmp/cxl_offline_range.env ]; then
        source /tmp/cxl_offline_range.env
    fi
fi

if [ -n "${CXL_OFFLINE_BASE:-}" ] && [ -n "${CXL_OFFLINE_SIZE:-}" ]; then
    rmmod cxl_memtype 2>/dev/null || true
    if insmod "$SCRIPT_DIR/../kmod/cxl_memtype.ko" \
        cxl_phys_base=$CXL_OFFLINE_BASE \
        cxl_phys_size=$CXL_OFFLINE_SIZE; then
        ls -la /dev/cxl_uc /dev/cxl_wc 2>/dev/null || true
        dmesg 2>/dev/null | tail -5 || true
    else
        echo "  WARNING: insmod failed (exit $?). Module refused to load."
        echo "  Check dmesg for details. UC/WC modes will not work."
        dmesg 2>/dev/null | tail -5 || true
    fi
else
    echo "  WARNING: No offlined range. Set CXL_OFFLINE_BASE and CXL_OFFLINE_SIZE, or run step1_offline_cxl.sh."
    echo "  UC/WC modes will not work until CXL memory is offlined and module loaded with that range."
fi

# ---- 5. Huge pages on CXL and local node -----------------------------
# Allocated AFTER offline so hugepages don't land on the offlined range.
echo "[5/7] Huge pages on NUMA node $CXL_NODE ..."
HP="/sys/devices/system/node/node${CXL_NODE}/hugepages/hugepages-2048kB/nr_hugepages"
[ -f "$HP" ] && echo 2048 > "$HP" 2>/dev/null && echo "  $(cat "$HP") huge pages on node $CXL_NODE"

HP_L="/sys/devices/system/node/node${LOCAL_NODE}/hugepages/hugepages-2048kB/nr_hugepages"
[ -f "$HP_L" ] && echo 64 > "$HP_L" 2>/dev/null

# ---- 6. Mount resctrl for CAT/MBA -------------------------------------
echo "[6/7] Mounting resctrl ..."
if ! mountpoint -q /sys/fs/resctrl 2>/dev/null; then
    mount -t resctrl resctrl /sys/fs/resctrl 2>/dev/null || true
fi
[ -f /sys/fs/resctrl/info/L3/cbm_mask ] && cat /sys/fs/resctrl/info/L3/cbm_mask && echo " (L3 CBM)"
[ -f /sys/fs/resctrl/info/MB/bandwidth_gran ] && cat /sys/fs/resctrl/info/MB/bandwidth_gran && echo " (MBA gran)"

# ---- 7. Verify perf events (AMD L2) ------------------------------------
echo "[7/7] Verifying AMD perf events ..."
perf stat -e r7064,r0864 -a sleep 0.1 2>&1 | tail -5 || echo "  (If not supported, check kernel/perf and use Intel codes on Intel CPUs)"
echo ""
echo "=== CCD topology (first 20 cores) ==="
lscpu -e=CPU,CORE,SOCKET,NODE,L2,L3 2>/dev/null | head -22
echo ""
echo "=== Setup complete ==="
