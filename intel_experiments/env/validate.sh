#!/usr/bin/env bash
# env/validate.sh — Pre-flight checks; exits non-zero if environment is wrong
# Run after sudo env/setup.sh. No root required to run validate.sh itself.

set -euo pipefail
# Avoid SIGPIPE failures from grep|head pipelines
trap '' PIPE

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FAIL=0
WARN=0

pass()  { echo "  [PASS] $*"; }
fail()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn()  { echo "  [WARN] $*"; WARN=$((WARN+1)); }
header(){ echo ""; echo "=== $* ==="; }

echo "=== directory-tax-spr env/validate.sh ==="
echo "Date: $(date --iso-8601=seconds)"
echo "User: $(id -un)"

# ── CPU identity ──────────────────────────────────────────────────────────────
header "CPU Identity"
MODEL=$(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2 | xargs)
if echo "$MODEL" | grep -qi "8462Y"; then
    pass "CPU: $MODEL"
else
    fail "Expected 8462Y+, found: $MODEL"
fi

STEPPING=$(grep -m1 "^stepping" /proc/cpuinfo | awk '{print $3}')
if [[ "$STEPPING" -eq 8 ]]; then
    pass "Stepping: 8 (expected for SPR D0)"
else
    warn "Stepping: $STEPPING (expected 8 for SPR D0)"
fi

# ── NUMA topology ─────────────────────────────────────────────────────────────
header "NUMA Topology"
NODES=$(ls /sys/devices/system/node/ | grep -c "^node[0-9]" || true)
if [[ "$NODES" -ge 2 ]]; then
    pass "NUMA nodes: $NODES"
else
    fail "Expected ≥2 NUMA nodes, found: $NODES"
fi

# Verify node 0 has CPUs 0-31 (first physical cores of socket 0)
NODE0_CPUS=$(cat /sys/devices/system/node/node0/cpulist 2>/dev/null || echo "MISSING")
if echo "$NODE0_CPUS" | grep -q "^0-31"; then
    pass "Node 0 CPUs: $NODE0_CPUS"
else
    warn "Node 0 CPU list unexpected: $NODE0_CPUS (expected to start with 0-31)"
fi

# ── perf_event_paranoid ───────────────────────────────────────────────────────
header "perf_event_paranoid"
PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid)
if [[ "$PARANOID" -le 0 ]]; then
    pass "perf_event_paranoid = $PARANOID"
else
    fail "perf_event_paranoid = $PARANOID (need ≤ 0 for uncore PMU access; run sudo setup.sh)"
fi

# ── Uncore PMU access ─────────────────────────────────────────────────────────
header "Uncore PMU Devices"
CHA_COUNT=$(ls /sys/bus/event_source/devices/ 2>/dev/null | grep -c "^uncore_cha_" || true)
if [[ "$CHA_COUNT" -ge 32 ]]; then
    pass "CHA tiles visible: $CHA_COUNT"
else
    fail "Expected ≥32 CHA tiles, found: $CHA_COUNT"
fi

# Verify specific SF eviction events are present
if perf list 2>/dev/null | grep -q "unc_cha_core_snp.evict_one"; then
    pass "PMU event unc_cha_core_snp.evict_one present"
else
    fail "PMU event unc_cha_core_snp.evict_one NOT FOUND — SF measurement impaired"
fi

if perf list 2>/dev/null | grep -q "unc_cha_rxc_req_q1_retry.sf_victim"; then
    pass "PMU event unc_cha_rxc_req_q1_retry.sf_victim present"
else
    warn "PMU event unc_cha_rxc_req_q1_retry.sf_victim not found (secondary metric)"
fi

# Test actual uncore perf read (requires paranoid ≤ 0)
if [[ "$PARANOID" -le 0 ]]; then
    if perf stat -e uncore_cha_0/unc_cha_clockticks/ -a -- sleep 0.1 &>/dev/null; then
        pass "Uncore perf read: functional"
    else
        warn "Uncore perf stat failed — may need CAP_PERFMON or root for uncore"
    fi
fi

# ── Turbo / Frequency ─────────────────────────────────────────────────────────
header "CPU Frequency and Turbo"
NO_TURBO=$(cat /sys/devices/system/cpu/intel_pstate/no_turbo 2>/dev/null || echo "MISSING")
if [[ "$NO_TURBO" == "1" ]]; then
    pass "Turbo: disabled (no_turbo=1)"
elif [[ "$NO_TURBO" == "MISSING" ]]; then
    fail "intel_pstate/no_turbo not found"
else
    fail "Turbo: ENABLED (no_turbo=0) — frequency instability will corrupt measurements"
fi

GOV=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null || echo "MISSING")
if [[ "$GOV" == "performance" ]]; then
    pass "Governor: performance"
else
    fail "Governor: $GOV (need performance; run sudo setup.sh)"
fi

MIN_FREQ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_min_freq 2>/dev/null || echo "0")
MAX_FREQ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq 2>/dev/null || echo "0")
if [[ "$MIN_FREQ" == "$MAX_FREQ" ]] && [[ "$MIN_FREQ" -gt 2000000 ]]; then
    pass "Frequency locked: $((MIN_FREQ / 1000)) MHz"
else
    fail "Frequency not locked: min=$((MIN_FREQ/1000)) MHz, max=$((MAX_FREQ/1000)) MHz"
fi

# ── MSR access ────────────────────────────────────────────────────────────────
header "MSR 0x1A4 Access"
if ! command -v rdmsr &>/dev/null; then
    fail "rdmsr not found (run: sudo apt-get install msr-tools)"
else
    pass "rdmsr found: $(which rdmsr)"
fi

if [[ -c /dev/cpu/0/msr ]]; then
    if [[ -r /dev/cpu/0/msr ]] && [[ -w /dev/cpu/0/msr ]]; then
        pass "/dev/cpu/0/msr readable+writable by $(id -un)"
    else
        fail "/dev/cpu/0/msr not accessible by $(id -un) — run sudo setup.sh to fix permissions"
    fi
else
    fail "/dev/cpu/0/msr device not found — check 'modprobe msr'"
fi

# Check MSR write capability via setcap on stream_wb_nopf
# (rdmsr as user always fails: kernel MSR driver checks CAP_SYS_RAWIO regardless of device perms)
NOPF_BIN="$PROJECT_ROOT/bench/aggressor/stream_wb_nopf"
if command -v getcap &>/dev/null; then
    if [[ -f "$NOPF_BIN" ]]; then
        CAPS=$(getcap "$NOPF_BIN" 2>/dev/null || echo "NONE")
        if echo "$CAPS" | grep -q "cap_sys_rawio"; then
            pass "stream_wb_nopf has cap_sys_rawio (condition B MSR access OK)"
        else
            fail "stream_wb_nopf lacks cap_sys_rawio — run: sudo env/setup.sh after make -C bench/"
        fi
    else
        warn "stream_wb_nopf not built yet — run make -C bench/, then sudo env/setup.sh"
    fi
else
    warn "getcap not found — cannot verify MSR capability (install libcap2-bin)"
fi

# ── NUMA balancing ────────────────────────────────────────────────────────────
header "NUMA Balancing"
NB=$(cat /proc/sys/kernel/numa_balancing 2>/dev/null || echo "MISSING")
if [[ "$NB" == "0" ]]; then
    pass "numa_balancing: disabled"
else
    fail "numa_balancing = $NB (must be 0; run sudo setup.sh)"
fi

# ── ASLR ──────────────────────────────────────────────────────────────────────
header "ASLR"
ASLR=$(cat /proc/sys/kernel/randomize_va_space 2>/dev/null || echo "MISSING")
if [[ "$ASLR" -le 1 ]]; then
    pass "randomize_va_space = $ASLR (acceptable)"
else
    warn "randomize_va_space = $ASLR (2 = full ASLR; prefer 1 for reproducibility)"
fi

# ── Hugepages ─────────────────────────────────────────────────────────────────
header "Hugepages"
NODE0_HP_PATH="/sys/devices/system/node/node0/hugepages/hugepages-2048kB"
HP2M_N0=$(cat "$NODE0_HP_PATH/nr_hugepages" 2>/dev/null || echo "0")
HP2M_FREE_N0=$(cat "$NODE0_HP_PATH/free_hugepages" 2>/dev/null || echo "0")
HP2M_SYS=$(cat /sys/kernel/mm/hugepages/hugepages-2048kB/nr_hugepages 2>/dev/null || echo "0")
if [[ "$HP2M_N0" -ge 4096 ]]; then
    pass "2MB hugepages on node 0: $HP2M_N0 (≥4096 = 8 GB minimum for 8 aggressor cores)"
else
    fail "2MB hugepages on node 0: $HP2M_N0 (need ≥4096; run sudo env/setup.sh to allocate)"
fi
if [[ "$HP2M_FREE_N0" -ge 4096 ]]; then
    pass "2MB hugepages free on node 0: $HP2M_FREE_N0"
elif [[ "$HP2M_FREE_N0" -ge 1024 ]]; then
    warn "2MB hugepages free on node 0: $HP2M_FREE_N0 (may limit aggressor count)"
else
    fail "2MB hugepages free on node 0: $HP2M_FREE_N0 (nearly exhausted)"
fi
pass "2MB hugepages system total: $HP2M_SYS"

# ── Required tools ────────────────────────────────────────────────────────────
header "Required Tools"
for tool in gcc python3 numactl perf rdmsr; do
    if command -v "$tool" &>/dev/null; then
        pass "$tool: $(which $tool)"
    else
        fail "$tool: NOT FOUND"
    fi
done

# Python packages
for pkg in numpy scipy statsmodels matplotlib pandas; do
    if python3 -c "import $pkg" &>/dev/null; then
        pass "python3 $pkg: OK"
    else
        fail "python3 $pkg: NOT INSTALLED (run: pip3 install $pkg)"
    fi
done

# ── Bench binaries (post-compilation check) ───────────────────────────────────
header "Bench Binaries (run after make)"
for bin in victim/pointer_chase aggressor/stream_wb aggressor/stream_wb_nopf \
           aggressor/stream_wc aggressor/stream_nt; do
    if [[ -x "$PROJECT_ROOT/bench/$bin" ]]; then
        pass "bench/$bin: compiled"
    else
        warn "bench/$bin: not compiled (run make -C bench/ first)"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Validation summary: $FAIL failures, $WARN warnings"
if [[ "$FAIL" -eq 0 ]]; then
    echo "RESULT: PASS — environment ready for Phase 1"
    exit 0
else
    echo "RESULT: FAIL — $FAIL check(s) must be resolved before Phase 1"
    echo ""
    echo "Most likely fix: sudo env/setup.sh"
    echo "Then re-run: env/validate.sh"
    exit 1
fi
