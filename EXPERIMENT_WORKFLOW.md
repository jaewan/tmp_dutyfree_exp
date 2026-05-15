# Corrected Experimental Workflow

This document describes the corrected experiment flow after fixing PAT aliasing, AMD perf events, victim working-set sizing, and CCD-aware core selection.

## CRITICAL: PAT Aliasing Limitation

On x86, **offlining memory does NOT remove it from the kernel's linear/direct map**.
The direct map still has WB PTEs for offlined pages. Creating UC/WC mappings via
`remap_pfn_range()` produces conflicting cache types for the same physical page.

**Consequence**: UC/WC mappings silently resolve to WB. All five modes produce
identical bandwidth (~12.7 GB/s on this system). This was confirmed in our first
exp1 run.

**Correct UC/WC approach** (requires further work):
1. **Device-DAX** (`daxctl`): Convert CXL region from `system-ram` to `devdax` mode,
   which properly removes it from the direct map.
2. **`/dev/mem`**: Map via `/dev/mem` with `O_SYNC` (some kernels restrict this).
3. **`ioremap_uc` in kernel**: Do all UC/WC I/O from the kernel module itself.

**For now**: WB experiments (exp1-WB, exp2, exp3, exp4, exp5) are valid and
represent the real-world scenario (prefetchers + coherence). UC/WC modes require
the direct-map problem to be solved first.

---

## Prerequisites

- **Step 0** must be run first. Save full output; it drives L2 size, perf event codes, and topology.
- **Validation (Step 4)** is the gate: do not proceed to exp2–5 until WB >> UC bandwidth.

---

## Step 0: Characterize Your System

Run as root and save output. Every downstream decision depends on it.

```bash
sudo bash scripts/step0_characterize.sh | tee step0_output.txt
```

From the output:

1. **L2 size** — Set `L2_SIZE_BYTES` in `src/common.h` (or build with `-DL2_SIZE_BYTES=524288` for 512 KB Bergamo).
2. **Perf events** — AMD Zen 4 uses `r7064` / `r0864`; Intel uses `0x02D1` / `0x10D1`. `common.h` is set for AMD by default.
3. **CXL range** — Needed for offlining and module parameters.

---

## Step 1: Offline CXL Memory for UC/WC

UC and WC mappings require the target physical range to be **offlined** from System RAM to avoid PAT aliasing with the kernel direct map.

```bash
sudo bash scripts/step1_offline_cxl.sh
```

This offlines 8 GB at the end of the CXL node and writes `CXL_OFFLINE_BASE` and `CXL_OFFLINE_SIZE` to `/tmp/cxl_offline_range.env`.

---

## Step 2: Corrected Kernel Module

The module creates `/dev/cxl_uc` and `/dev/cxl_wc` with correct PAT attributes **only** when given an offlined physical range. Build and load:

```bash
make kmod
# Load is done by setup.sh using /tmp/cxl_offline_range.env
```

---

## Step 3: System Setup (AMD-Corrected)

```bash
sudo bash scripts/setup.sh
```

This script:

- Sets performance governor and disables **AMD** boost (`/sys/devices/system/cpu/cpufreq/boost`) or Intel turbo.
- Allocates huge pages on CXL and local node.
- Sources `/tmp/cxl_offline_range.env` and loads `cxl_memtype` with that range (or runs step1 if the env file is missing).
- Mounts resctrl for CAT/MBA.
- Verifies AMD perf events (`r7064`, `r0864`).

---

## Step 4: Validation Gate (Mandatory)

**Do not proceed to exp2–5 until this shows WB >> UC bandwidth.**

```bash
make clean && make
sudo ./bin/validate -c 0
```

**Expected (correct):**

- WB + AVX2 load: ~5–15 GB/s  
- WC + MOVNTDQA: ~1–5 GB/s  
- UC + scalar: ~0.05–0.3 GB/s  

**If UC ≈ WB:** Mapping is broken (PAT aliasing). Ensure memory was offlined (step1) and the module was loaded with `CXL_OFFLINE_BASE` / `CXL_OFFLINE_SIZE`. Debug with:

```bash
grep <addr_prefix> /proc/<pid>/smaps
cat /sys/kernel/debug/x86/pat_memtype_list
cat /sys/devices/system/memory/memory*/state
```

---

## Step 5: Core List Selection (CCD-Aware)

Non-monotonic scaling at 8/16 threads is often a CCD-boundary effect. Use a single-CCD core list for aggressors:

```bash
bash scripts/identify_ccds.sh
```

Use the suggested single-CCD core list in exp1/exp2. Reserve core 0 (or a core in a different CCD) for the victim to measure cross-CCD coherence effects.

---

## Step 6: Experiment Workflow

```bash
# 0. One-time: characterize and offline
sudo bash scripts/step0_characterize.sh | tee step0_output.txt
sudo bash scripts/step1_offline_cxl.sh
sudo bash scripts/setup.sh

# 1. GATE: Validate memory types
make clean && make
sudo ./bin/validate -c 0
# MUST see WB >> UC. If not, stop and debug.

# 2. Bandwidth characterization (exp1)
# Use CCD-aware core list from identify_ccds.sh
sudo bash scripts/exp1_bandwidth.sh

# 3. Victim degradation (exp2)
# Victim on core 0, aggressor on cores from one CCD; working set = 75% L2
sudo bash scripts/exp2_isobw.sh

# 4. CAT (exp3)
# On AMD, L3 is per-CCD. Put victim in same CCD as some aggressors to test L3 partitioning.
sudo bash scripts/exp3_cat.sh

# 5. MBA (exp4)
sudo bash scripts/exp4_mba.sh
```

---

## Step 7: AMD-Specific Measurements (Paper)

These strengthen the AMD characterization:

```bash
# L3 miss rate on victim under CXL aggressor (amd_l3 uncore PMU)
perf list | grep amd_l3
perf stat -e amd_l3/event=0x04,umask=0x01/ -e amd_l3/event=0x04,umask=0xff/ --per-core -C 0 sleep 15

# Data Fabric bandwidth
perf list | grep amd_df
```

For the paper’s AMD section:

1. **BW gap** — WB >> UC proves prefetcher dependence on WB (universal).
2. **L3 pollution** — Victim L3 miss rate rises under WB aggressor (AMD-specific).
3. **CAT** — On AMD, L3 CAT may help when aggressor shares the CCD (unlike Intel, where SF is the bottleneck).
4. **IPC** — Smaller drop than Intel (no L2 destruction) but measurable via L3 pressure.

---

## Summary Checklist

| Item | Action |
|------|--------|
| Kernel module | Uses offlined range; `remap_pfn_range` with correct pgprot; single-arg `class_create` (≥ 6.4). |
| PAT aliasing | Offline CXL blocks (step1) before UC/WC mapping. |
| Perf events | AMD: `r7064` / `r0864`; Intel: `0x02D1` / `0x10D1`. Set in `common.h`. |
| Victim working set | Size to ~75% of actual L2 via `L2_SIZE_BYTES` and `VICTIM_ARRAY_KB`. |
| Turbo/boost | AMD: `/sys/devices/system/cpu/cpufreq/boost`; Intel: `intel_pstate/no_turbo`. |
| Core selection | Use `identify_ccds.sh`; single-CCD aggressor list for clean scaling. |
| Validation | Run `bin/validate`; block on WB >> UC before exp2–5. |
