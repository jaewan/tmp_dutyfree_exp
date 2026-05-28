#!/usr/bin/env bash
# env/setup.sh — Idempotent system configuration for directory-tax-spr
# Must be run as root: sudo env/setup.sh [--target-freq-mhz N]

# Use set -eu (no pipefail — pipefail kills loops on head/grep SIGPIPE).
# Trap SIGPIPE explicitly to prevent inherited pipe failures.
set -eu
trap '' PIPE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TARGET_FREQ_MHZ="${1:-3000}"
TARGET_FREQ_KHZ=$((TARGET_FREQ_MHZ * 1000))
EXPERIMENT_USER="${SUDO_USER:-domin}"
LOG="$SCRIPT_DIR/setup_run.log"

tlog() { echo "$*" | tee -a "$LOG"; }

tlog "=== directory-tax-spr setup.sh ==="
tlog "Date: $(date --iso-8601=seconds)"
tlog "Running as: $(id)"
tlog "Target frequency: ${TARGET_FREQ_MHZ} MHz"
tlog "Experiment user: ${EXPERIMENT_USER}"
tlog "Project root: ${PROJECT_ROOT}"

if [[ $EUID -ne 0 ]]; then
    tlog "ERROR: must run as root (sudo env/setup.sh)"
    exit 1
fi

# ── 1. Install msr-tools ─────────────────────────────────────────────────────
tlog ""
tlog "=== Step 1: msr-tools ==="
if ! command -v rdmsr &>/dev/null; then
    apt-get install -y msr-tools 2>&1 | tee -a "$LOG"
    tlog "  msr-tools installed"
else
    tlog "  msr-tools already present: $(which rdmsr)"
fi

# ── 2. Ensure msr module is loaded ───────────────────────────────────────────
tlog "=== Step 2: msr kernel module ==="
modprobe msr
tlog "  msr module loaded"

# ── 3. Grant MSR device ownership to experiment user ─────────────────────────
# Note: ownership grants open()+pread() access but CAP_SYS_RAWIO is ALSO needed
# by the kernel's MSR driver — granted to bench binaries via setcap in Step 14.
tlog "=== Step 3: MSR device permissions ==="
for cpu_msr in /dev/cpu/*/msr; do
    chmod 0660 "$cpu_msr"
    chown "${EXPERIMENT_USER}:${EXPERIMENT_USER}" "$cpu_msr" 2>/dev/null || true
done
tlog "  /dev/cpu/*/msr: owner=${EXPERIMENT_USER}, mode=0660"

# ── 4. Record baseline MSR 0x1A4 (prefetcher state) — root reads fine ────────
tlog "=== Step 4: Baseline MSR 0x1A4 (prefetcher state, as root) ==="
BASELINE_MSR_FILE="$SCRIPT_DIR/baseline_msr_1a4.txt"
echo "# Baseline MSR 0x1A4 per core — captured $(date --iso-8601=seconds)" > "$BASELINE_MSR_FILE"
for cpu in $(seq 0 31); do
    val=$(rdmsr -p "$cpu" 0x1A4 2>/dev/null || echo "FAIL")
    printf "cpu%-3d: %s\n" "$cpu" "$val" >> "$BASELINE_MSR_FILE"
done
tlog "  Full dump: $BASELINE_MSR_FILE"
head -4 "$BASELINE_MSR_FILE" | tee -a "$LOG" || true

BASELINE_CPU0=$(rdmsr -p 0 0x1A4 2>/dev/null || echo "FAIL")
tlog "  CPU0 baseline MSR 0x1A4 = $BASELINE_CPU0"
if [[ "$BASELINE_CPU0" != "0" ]] && [[ "$BASELINE_CPU0" != "0x0" ]] && [[ "$BASELINE_CPU0" != "FAIL" ]]; then
    tlog "  WARNING: CPU0 MSR 0x1A4 = $BASELINE_CPU0 (expected 0 — some prefetchers BIOS-disabled)"
fi

# ── 5. perf_event_paranoid ───────────────────────────────────────────────────
tlog "=== Step 5: perf_event_paranoid ==="
echo -1 > /proc/sys/kernel/perf_event_paranoid
PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid)
tlog "  perf_event_paranoid = $PARANOID"
if [[ "$PARANOID" -ne -1 ]]; then
    tlog "  ERROR: could not set perf_event_paranoid to -1 (got $PARANOID)"
    exit 1
fi

# ── 6. Disable turbo ─────────────────────────────────────────────────────────
tlog "=== Step 6: Disable turbo ==="
PSTATE_NO_TURBO="/sys/devices/system/cpu/intel_pstate/no_turbo"
if [[ -f "$PSTATE_NO_TURBO" ]]; then
    echo 1 > "$PSTATE_NO_TURBO"
    TURBO=$(cat "$PSTATE_NO_TURBO")
    tlog "  intel_pstate/no_turbo = $TURBO"
    if [[ "$TURBO" -ne 1 ]]; then
        tlog "  WARNING: could not disable turbo — may be BIOS-locked; proceeding"
    fi
else
    tlog "  WARNING: intel_pstate/no_turbo not found"
fi

# ── 7. Lock CPU frequency ─────────────────────────────────────────────────────
tlog "=== Step 7: CPU frequency lock to ${TARGET_FREQ_MHZ} MHz ==="
# intel_pstate with HWP may ignore cpupower; try anyway and report outcome.
if cpupower -c all frequency-set -g performance 2>&1 | tail -1 | tee -a "$LOG"; then
    tlog "  governor set: OK"
else
    tlog "  WARNING: cpupower governor set failed (intel_pstate HWP may override)"
fi
if cpupower -c all frequency-set -d "${TARGET_FREQ_KHZ}" -u "${TARGET_FREQ_KHZ}" 2>&1 | tail -1 | tee -a "$LOG"; then
    tlog "  frequency range set: OK"
else
    tlog "  WARNING: cpupower frequency set failed"
fi
sleep 1
ACTUAL_KHZ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo "unknown")
ACTUAL_GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "unknown")
tlog "  cpu0 governor=${ACTUAL_GOV}, cur_freq=${ACTUAL_KHZ} kHz"
if [[ "$ACTUAL_KHZ" != "unknown" ]]; then
    LOWER=$((TARGET_FREQ_KHZ * 80 / 100))
    if [[ "$ACTUAL_KHZ" -lt "$LOWER" ]]; then
        tlog "  WARNING: cpu0 freq ${ACTUAL_KHZ} kHz far below target ${TARGET_FREQ_KHZ} kHz"
        tlog "  HWP may be active. Consider: echo 0 > /sys/devices/system/cpu/cpufreq/policy0/energy_performance_preference"
    fi
fi

# ── 7b. Try HWP energy-performance preference if available ───────────────────
tlog "=== Step 7b: HWP energy-performance preference ==="
HWP_SET=0
for pol in /sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference; do
    if [[ -f "$pol" ]]; then
        echo "performance" > "$pol" 2>/dev/null && HWP_SET=$((HWP_SET+1)) || true
    fi
done
if [[ "$HWP_SET" -gt 0 ]]; then
    tlog "  HWP energy_performance_preference set to 'performance' on $HWP_SET policies"
else
    tlog "  HWP energy_performance_preference: not applicable or not found"
fi
sleep 1
ACTUAL_KHZ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo "unknown")
tlog "  cpu0 freq after HWP set: ${ACTUAL_KHZ} kHz"

# ── 8. Disable NUMA balancing ─────────────────────────────────────────────────
tlog "=== Step 8: NUMA balancing ==="
echo 0 > /proc/sys/kernel/numa_balancing
tlog "  numa_balancing = $(cat /proc/sys/kernel/numa_balancing)"

# ── 9. ASLR ──────────────────────────────────────────────────────────────────
tlog "=== Step 9: ASLR ==="
echo 1 > /proc/sys/kernel/randomize_va_space
tlog "  randomize_va_space = $(cat /proc/sys/kernel/randomize_va_space)"

# ── 10. RDPMC ────────────────────────────────────────────────────────────────
tlog "=== Step 10: RDPMC ==="
RDPMC_PATH="/sys/bus/event_source/devices/cpu/rdpmc"
if [[ -f "$RDPMC_PATH" ]]; then
    echo 2 > "$RDPMC_PATH"
    tlog "  rdpmc = $(cat $RDPMC_PATH)"
else
    tlog "  rdpmc sysfs not found — skipping"
fi

# ── 11. THP: leave as madvise ────────────────────────────────────────────────
tlog "=== Step 11: Transparent Hugepages ==="
tlog "  THP = $(cat /sys/kernel/mm/transparent_hugepage/enabled)"

# ── 12. Allocate 2MB hugepages on NUMA node 0 ────────────────────────────────
tlog "=== Step 12: 2MB hugepages on NUMA node 0 ==="
HP_TARGET="${NODE0_HP_TARGET:-24576}"
NODE0_HP_PATH="/sys/devices/system/node/node0/hugepages/hugepages-2048kB/nr_hugepages"
if [[ -f "$NODE0_HP_PATH" ]]; then
    CURRENT_HP=$(cat "$NODE0_HP_PATH")
    tlog "  Node 0 2MB hugepages before: $CURRENT_HP"
    if [[ "$CURRENT_HP" -lt "$HP_TARGET" ]]; then
        echo "$HP_TARGET" > "$NODE0_HP_PATH"
        sleep 2
        AFTER_HP=$(cat "$NODE0_HP_PATH")
        tlog "  Node 0 2MB hugepages after: $AFTER_HP"
        if [[ "$AFTER_HP" -lt 4096 ]]; then
            tlog "  WARNING: only $AFTER_HP hugepages allocated (need ≥4096 for 8 GB)"
            tlog "  Possible cause: insufficient contiguous physical memory"
            tlog "  Workaround: reduce aggressor --region-gb to 0.5 in experiment scripts"
        fi
    else
        tlog "  Node 0 already has $CURRENT_HP hugepages"
    fi
else
    tlog "  WARNING: per-node hugepage path not found; trying system-wide"
    echo "$HP_TARGET" > /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || true
fi
HP2M_SYS=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo "N/A")
tlog "  2M hugepages system total: $HP2M_SYS"

# ── 12b. Phase 19 CXL hugepages on NUMA node 2 ───────────────────────────────
tlog "=== Step 12b: Phase 19 CXL hugepages on NUMA node 2 ==="
PHASE19_NODE2_HP_TARGET="${NODE2_HP_TARGET:-24576}"
NODE2_HP_DIR="/sys/devices/system/node/node2/hugepages/hugepages-2048kB"
NODE2_HP_PATH="$NODE2_HP_DIR/nr_hugepages"
if [[ -d /sys/devices/system/node/node2 ]]; then
    NODE2_CPUS=$(cat /sys/devices/system/node/node2/cpulist 2>/dev/null || true)
    NODE2_MEM_MB=$(numactl --hardware 2>/dev/null | awk '/node 2 size:/ {print $4}' || echo "unknown")
    tlog "  Node 2 present: cpulist='${NODE2_CPUS}', size=${NODE2_MEM_MB} MB"
    if [[ -f "$NODE2_HP_PATH" ]]; then
        CURRENT_HP_N2=$(cat "$NODE2_HP_PATH")
        tlog "  Node 2 2MB hugepages before: $CURRENT_HP_N2"
        if [[ "$CURRENT_HP_N2" -lt "$PHASE19_NODE2_HP_TARGET" ]]; then
            echo "$PHASE19_NODE2_HP_TARGET" > "$NODE2_HP_PATH"
            sleep 2
            AFTER_HP_N2=$(cat "$NODE2_HP_PATH")
            FREE_HP_N2=$(cat "$NODE2_HP_DIR/free_hugepages" 2>/dev/null || echo "0")
            tlog "  Node 2 2MB hugepages after: $AFTER_HP_N2 (free=$FREE_HP_N2)"
            if [[ "$AFTER_HP_N2" -lt 2048 ]]; then
                tlog "  WARNING: node 2 hugepage allocation is too small for Phase 19 calibration"
                tlog "  If CXL memory is fragmented, reboot and run setup before other allocations"
            elif [[ "$AFTER_HP_N2" -lt 16384 ]]; then
                tlog "  WARNING: node 2 has <16384 hugepages (<32 GB); Phase 19 core counts may be limited"
            fi
        else
            FREE_HP_N2=$(cat "$NODE2_HP_DIR/free_hugepages" 2>/dev/null || echo "0")
            tlog "  Node 2 already has $CURRENT_HP_N2 hugepages (free=$FREE_HP_N2)"
        fi
    else
        tlog "  WARNING: node 2 hugepage path not found; CXL node may not support hugetlb allocation"
    fi
else
    tlog "  WARNING: NUMA node 2 not present; Phase 19 CXL experiments cannot run"
fi
HP2M_SYS=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo "N/A")
tlog "  2M hugepages system total after Phase 19 reservation: $HP2M_SYS"
tlog "  Override targets with: sudo env NODE0_HP_TARGET=16384 NODE2_HP_TARGET=16384 ./env/setup.sh"

# ── 12c. Mount resctrl for CAT/MBA (Mitigation Trap Plan Phase 2/3) ──────────
tlog "=== Step 12c: resctrl (CAT/MBA) ==="
if mountpoint -q /sys/fs/resctrl; then
    tlog "  resctrl already mounted at /sys/fs/resctrl"
elif mount -t resctrl resctrl /sys/fs/resctrl 2>>"$LOG"; then
    tlog "  resctrl mounted at /sys/fs/resctrl"
else
    tlog "  WARNING: could not mount resctrl (CAT/MBA Phase 2/3 will be unavailable)"
    tlog "  Check CPU support: grep -o -E 'cat_l3|mba' /proc/cpuinfo | sort -u"
fi
if mountpoint -q /sys/fs/resctrl; then
    L3_CBM=$(cat /sys/fs/resctrl/info/L3/cbm_mask 2>/dev/null || echo "N/A")
    L3_MINBITS=$(cat /sys/fs/resctrl/info/L3/min_cbm_bits 2>/dev/null || echo "N/A")
    MB_MIN=$(cat /sys/fs/resctrl/info/MB/min_bandwidth 2>/dev/null || echo "N/A")
    MB_GRAN=$(cat /sys/fs/resctrl/info/MB/bandwidth_gran 2>/dev/null || echo "N/A")
    tlog "  L3 cbm_mask=$L3_CBM min_cbm_bits=$L3_MINBITS | MB min_bandwidth=$MB_MIN gran=$MB_GRAN"
fi

# ── 12d. Optional 1GB hugepages on the CXL node (Phase 1c TLB isolation) ─────
# Phase 1c's aggressor allocates its stream buffer on the CXL node, so reserve
# the 1GB pages there. Override the node with CXL_NODE (default 2).
HP1G_NODE="${CXL_NODE:-2}"
tlog "=== Step 12d: optional 1GB hugepages on NUMA node $HP1G_NODE (CXL) ==="
HP1G_TARGET="${NODE_HP1G_TARGET:-0}"
HP1G_PATH="/sys/devices/system/node/node${HP1G_NODE}/hugepages/hugepages-1048576kB/nr_hugepages"
if [[ "$HP1G_TARGET" -gt 0 ]]; then
    if [[ -f "$HP1G_PATH" ]]; then
        echo "$HP1G_TARGET" > "$HP1G_PATH" 2>>"$LOG" || true
        sleep 2
        AFTER_HP1G=$(cat "$HP1G_PATH")
        tlog "  Node $HP1G_NODE 1GB hugepages: requested $HP1G_TARGET, got $AFTER_HP1G"
        if [[ "$AFTER_HP1G" -lt "$HP1G_TARGET" ]]; then
            tlog "  WARNING: 1GB reservation short (needs contiguous memory; reboot may help)"
            tlog "  Phase 1c will skip the 1GB point if unavailable"
        fi
    else
        tlog "  WARNING: 1GB hugepage sysfs not found (pdpe1gb unsupported?)"
    fi
else
    tlog "  Skipped (set NODE_HP1G_TARGET=N to reserve N×1GB pages for Phase 1c)"
fi

# ── 13. Grant CAP_SYS_RAWIO to MSR-accessing benchmark binaries ──────────────
# Rationale: Linux MSR driver requires CAP_SYS_RAWIO regardless of device file
# permissions. setcap+ep grants this capability when any user exec's the binary.
# This is required for condition B and victim prefetcher ablation via MSR 0x1A4.
tlog "=== Step 13: setcap CAP_SYS_RAWIO for MSR-accessing binaries ==="
NOPF_BIN="$PROJECT_ROOT/bench/aggressor/stream_wb_nopf"
WC_NOPF_BIN="$PROJECT_ROOT/bench/aggressor/stream_wc_nopf"
VICTIM_BIN="$PROJECT_ROOT/bench/victim/pointer_chase"
TURNOVER_BIN="$PROJECT_ROOT/bench/aggressor/forced_turnover"
for MSR_BIN in "$NOPF_BIN" "$WC_NOPF_BIN" "$VICTIM_BIN" "$TURNOVER_BIN"; do
    if [[ -f "$MSR_BIN" ]]; then
        setcap cap_sys_rawio+ep "$MSR_BIN"
        CAPS=$(getcap "$MSR_BIN" 2>/dev/null || echo "FAIL")
        tlog "  $MSR_BIN: $CAPS"
        if echo "$CAPS" | grep -q "cap_sys_rawio"; then
            tlog "  setcap: OK — MSR access will work for user $EXPERIMENT_USER"
        else
            tlog "  WARNING: setcap did not stick for $MSR_BIN (filesystem noexec? Re-run after 'make -C bench/')"
        fi
    else
        tlog "  WARNING: $MSR_BIN not found — run 'make -C bench/' first, then re-run setup.sh"
        tlog "  (setcap is re-applied automatically by artifact/reproduce.sh after every build if configured)"
    fi
done

# ── 14. Final state snapshot ─────────────────────────────────────────────────
tlog "=== Step 14: Post-setup snapshot ==="
POST="$SCRIPT_DIR/env_report_post_setup.md"
{
    echo "# Post-Setup Environment Snapshot"
    echo "## $(date --iso-8601=seconds)"
    echo ""
    echo "| Setting | Value |"
    echo "|---------|-------|"
    printf "| perf_event_paranoid | %s |\n" "$(cat /proc/sys/kernel/perf_event_paranoid)"
    printf "| numa_balancing | %s |\n"      "$(cat /proc/sys/kernel/numa_balancing)"
    printf "| randomize_va_space | %s |\n"  "$(cat /proc/sys/kernel/randomize_va_space)"
    printf "| no_turbo | %s |\n"            "$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo N/A)"
    printf "| cpu0 governor | %s |\n"       "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo N/A)"
    printf "| cpu0 min_freq kHz | %s |\n"   "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq 2>/dev/null || echo N/A)"
    printf "| cpu0 max_freq kHz | %s |\n"   "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo N/A)"
    printf "| cpu0 cur_freq kHz | %s |\n"   "$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null || echo N/A)"
    printf "| MSR 0x1A4 cpu0 baseline | %s |\n" "$(rdmsr -p 0 0x1A4 2>/dev/null || echo FAIL)"
    printf "| node0 2M hugepages | %s |\n"  "$(cat /sys/devices/system/node/node0/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo N/A)"
    printf "| node0 free 2M hugepages | %s |\n" "$(cat /sys/devices/system/node/node0/hugepages/hugepages-2048kB/free_hugepages 2>/dev/null || echo N/A)"
    printf "| node2 present | %s |\n"       "$([[ -d /sys/devices/system/node/node2 ]] && echo YES || echo NO)"
    printf "| node2 2M hugepages | %s |\n"  "$(cat /sys/devices/system/node/node2/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo N/A)"
    printf "| node2 free 2M hugepages | %s |\n" "$(cat /sys/devices/system/node/node2/hugepages/hugepages-2048kB/free_hugepages 2>/dev/null || echo N/A)"
    printf "| stream_wb_nopf caps | %s |\n" "$(getcap $NOPF_BIN 2>/dev/null || echo MISSING)"
    printf "| pointer_chase caps | %s |\n" "$(getcap $VICTIM_BIN 2>/dev/null || echo MISSING)"
    echo ""
    echo "## MSR 0x1A4 Baseline"
    echo '```'
    cat "$BASELINE_MSR_FILE" 2>/dev/null || echo "(not captured)"
    echo '```'
} > "$POST"
tlog "  Written: $POST"

tlog ""
tlog "=== setup.sh complete ==="
tlog "Next: run env/validate.sh to confirm all conditions are met"
