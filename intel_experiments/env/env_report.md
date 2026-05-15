# Environment Report — Directory Tax SPR
## Auto-captured: 2026-05-10 (Phase 0)
## Status: PARTIAL — items marked [NEEDS ROOT] require setup.sh to be run as root

---

## CPU

| Field              | Value                                            |
|--------------------|--------------------------------------------------|
| Model              | Intel(R) Xeon(R) Platinum 8462Y+                |
| Family/Model/Step  | 6 / (Sapphire Rapids) / 8                        |
| Microcode          | 0x2b000639                                       |
| Sockets            | 2                                                |
| Cores per socket   | 32 physical (64 logical with HT)                 |
| Total logical CPUs | 128                                              |
| CPU max MHz        | 4100.0 (turbo)                                   |
| CPU min MHz        | 800.0                                            |
| MHz at capture     | 800–944 MHz (governor=powersave, throttled) ⚠️   |

## Cache Hierarchy (per core, socket 0)

| Level | Size per core | Notes                        |
|-------|---------------|------------------------------|
| L1d   | 32 KB         |                              |
| L2    | 2 MB          | private per core             |
| L3    | 60 MB total   | shared across 32 cores/socket |

LLC is **non-inclusive** of L1+L2.
Snoop Filter (SF) is **inclusive** of L1+L2 across all cores on socket.

## NUMA Topology

| Node | Socket | CPUs             | Memory  | Notes           |
|------|--------|------------------|---------|-----------------|
| 0    | 0      | 0-31, 64-95      | ~503 GB |                 |
| 1    | 1      | 32-63, 96-127    | ~504 GB |                 |

Node distances: 0→0=10, 0→1=21, 1→0=21, 1→1=10
SNC mode: **OFF** (two sockets, one NUMA domain per socket).

**Experiment uses: NUMA node 0 (socket 0) exclusively.**

## Memory

Estimated from sysfs: ~503 GB on node 0.
DDR5-4800 with 8 channels per socket (uncore_imc_0 – uncore_imc_7 confirmed).
dmidecode: **[NEEDS ROOT]** — exact DIMM configuration not captured.

## Hugepages

| Type | Count at capture | Notes                           |
|------|------------------|---------------------------------|
| 2MB  | 13312            | ~26 GB available on node 0      |
| 1GB  | 0                | Not allocated; 2MB is sufficient|

## OS / Kernel

| Field         | Value                            |
|---------------|----------------------------------|
| Kernel        | 6.8.0-79-generic                 |
| OS            | Ubuntu 22.04.5 LTS               |
| intel_iommu   | on (from cmdline)                |

## CPU Frequency / Turbo / Governor

| Setting                          | Value at capture | Target for experiment |
|----------------------------------|------------------|-----------------------|
| intel_pstate status              | active           | active                |
| no_turbo                         | 0 (turbo ON) ⚠️  | 1 (turbo OFF)         |
| scaling_governor (cpu0)          | powersave ⚠️     | performance           |
| scaling_min_freq                 | 800 MHz ⚠️       | 3000 MHz (locked)     |
| scaling_max_freq                 | 4100 MHz ⚠️      | 3000 MHz (locked)     |

## MSR 0x1A4 (Prefetcher Control)

| Field                  | Value at capture                     |
|------------------------|--------------------------------------|
| Device exists          | YES (/dev/cpu/0/msr)                 |
| msr kernel module      | loaded                               |
| rdmsr installed        | YES (/usr/sbin/rdmsr) ✓              |
| Access as domin        | DENIED (root-only) ⚠️                |
| Baseline value         | [NEEDS ROOT — capture in setup.sh]   |
| Bits [3:0] meaning     | 0=L1DCU_stream, 1=DCU_IP, 2=L2adj, 3=L2stream |

Baseline MSR 0x1A4 value expected: 0x0 (all prefetchers enabled).
Experiment disables with: 0xF (all four disabled).

## Kernel Performance Settings

| Setting                              | Value at capture | Target          |
|--------------------------------------|------------------|-----------------|
| perf_event_paranoid                  | -1 ✓             | -1              |
| numa_balancing                       | 1 (enabled) ⚠️   | 0               |
| transparent_hugepage/enabled         | [madvise]        | madvise (OK)    |
| transparent_hugepage/defrag          | [madvise]        | madvise (OK)    |
| randomize_va_space (ASLR)            | 2 ⚠️             | 1               |
| isolcpus                             | none in cmdline  | n/a (use numactl) |

## Available Tools

| Tool       | Available | Version               |
|------------|-----------|-----------------------|
| gcc        | YES       | 11.4.0                |
| python3    | YES       | 3.10.12               |
| numactl    | YES       | installed             |
| taskset    | YES       | installed             |
| perf       | YES       | (matches kernel)      |
| cpupower   | YES       | installed             |
| rdmsr/wrmsr| NO ⚠️     | install msr-tools     |

## Uncore PMU Status

| Component          | Count | Accessible (paranoid=4) |
|--------------------|-------|-------------------------|
| uncore_cha_*       | 32    | NO ⚠️                   |
| uncore_imc_*       | 8     | NO ⚠️                   |
| uncore_iio_*       | 7     | NO ⚠️                   |

After `echo -1 > /proc/sys/kernel/perf_event_paranoid` (run by setup.sh):
all uncore PMU events will be accessible.

Key SF-related PMU events confirmed present in perf list:
- `unc_cha_core_snp.evict_one` — SF back-invalidation to one core
- `unc_cha_core_snp.evict_gtone` — SF back-invalidation to >1 core
- `unc_cha_rxc_req_q1_retry.sf_victim` — SF capacity pressure (retries)
- `unc_cha_rxc_irq1_reject.sf_victim` — SF victim rejection
- `unc_cha_llc_lookup.sf_e/sf_h/sf_s` — SF hit lookups
- `unc_cha_tor_inserts.ia_drd_pref` — prefetch-demand fills
- `unc_cha_tor_inserts.ia_drd` — demand reads

## Items Requiring setup.sh (run as root)

1. Install msr-tools: `apt-get install -y msr-tools`
2. Record baseline MSR 0x1A4: `rdmsr -p N 0x1A4` for all cores
3. Set perf_event_paranoid: `echo -1 > /proc/sys/kernel/perf_event_paranoid`
4. Disable turbo: `echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo`
5. Set governor: `cpupower -c all frequency-set -g performance`
6. Lock frequency (3.0 GHz): `cpupower -c all frequency-set -d 3000000 -u 3000000`
7. Disable NUMA balancing: `echo 0 > /proc/sys/kernel/numa_balancing`
8. Set ASLR to partial: `echo 1 > /proc/sys/kernel/randomize_va_space`
9. Grant MSR group access: `chmod 0664 /dev/cpu/*/msr && chgrp domin /dev/cpu/*/msr`
   OR add CAP_SYS_RAWIO to benchmark binary

## Validation Status

All items marked ⚠️ above must be resolved by setup.sh before Phase 1.
Run `env/validate.sh` after `sudo env/setup.sh` completes.
Expected outcome: validate.sh exits 0.

Current status: validate.sh will exit **non-zero** (multiple items not
configured). See NEGATIVE_RESULTS.md §N0 for the pre-setup state.

---

## Phase 19 CXL Environment Verification
## Captured: 2026-05-14

### `numactl --hardware`

```text
available: 3 nodes (0-2)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 128 129 130 131 132 133 134 135 136 137 138 139 140 141 142 143 144 145 146 147 148 149 150 151 152 153 154 155 156 157 158 159 160 161 162 163 164 165 166 167 168 169 170 171 172 173 174 175 176 177 178 179 180 181 182 183 184 185 186 187 188 189 190 191
node 0 size: 515389 MB
node 0 free: 508734 MB
node 1 cpus: 64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95 96 97 98 99 100 101 102 103 104 105 106 107 108 109 110 111 112 113 114 115 116 117 118 119 120 121 122 123 124 125 126 127 192 193 194 195 196 197 198 199 200 201 202 203 204 205 206 207 208 209 210 211 212 213 214 215 216 217 218 219 220 221 222 223 224 225 226 227 228 229 230 231 232 233 234 235 236 237 238 239 240 241 242 243 244 245 246 247 248 249 250 251 252 253 254 255
node 1 size: 516004 MB
node 1 free: 510760 MB
node 2 cpus:
node 2 size: 124913 MB
node 2 free: 124757 MB
node distances:
node     0    1    2
   0:   10   21   14
   1:   21   10   24
   2:   14   24   10
```

Verdict: node 2 exists, has zero CPUs, and exposes ~125 GB memory.

### `/sys/bus/cxl/devices`

```text
decoder0.0
decoder1.0
endpoint1
mem0
region0
root0
```

Verdict: CXL root, endpoint, memory device, and region are present.

### `cat /proc/iomem | grep -i cxl`

```text
00000000-00000000 : CXL Window 0
```

Verdict: CXL window is enumerated, but the kernel reports a redacted or
placeholder address range in `/proc/iomem`.

### `lspci -v | grep -i cxl`

```text
27:00.0 CXL: Montage Technology Co., Ltd. Device c000 (rev 01) (prog-if 10 [CXL Memory Device (CXL 2.0 or later)])
	Kernel driver in use: cxl_pci
	Kernel modules: cxl_pci
```

Verdict: CXL Type-3-class memory device is enumerated and bound to `cxl_pci`.

### Measurement Readiness Notes

Current host state before Phase 19 calibration:

```text
numa_balancing: 1
perf_event_paranoid: 4
cpu0 governor: powersave
intel_pstate/no_turbo: 0
```

This is sufficient to test whether CXL placement works, but not sufficient
for paper-grade Phase 19 matrix measurements. Before Phase 19.1, rerun the
root setup flow or otherwise set `numa_balancing=0`, `perf_event_paranoid<=0`,
governor `performance`, and turbo disabled.

### Phase 19 Basic CXL Allocation Test

Repository-equivalent command:

```bash
numactl --membind=2 --cpunodebind=0 -- ./bench/aggressor/stream_wb \
  --cpu 0 --node 2 --region-gb 1 --duration-sec 5 --no-verify
```

Result:

```text
exit_code=1
hugepage_alloc: mmap MAP_HUGETLB: Cannot allocate memory
  Hint: check 2MB hugepage availability with 'cat /proc/meminfo | grep HugePages_Free'
```

Huge page state at failure:

```text
/sys/devices/system/node/node0/hugepages/hugepages-2048kB/free_hugepages 0
/sys/devices/system/node/node1/hugepages/hugepages-2048kB/free_hugepages 0
/sys/devices/system/node/node2/hugepages/hugepages-2048kB/free_hugepages 0
HugePages_Total:       0
HugePages_Free:        0
Hugetlb:               0 kB
```

Verdict: Phase 19 is halted before calibration. CXL is enumerated, but the
current benchmark allocator cannot run until 2 MB huge pages are reserved,
including on node 2.

### Phase 19 Post-Setup Retry

After rerunning setup, hugepages and measurement settings were corrected:

```text
perf_event_paranoid=-1
numa_balancing=0
governor=performance
no_turbo=1
node0 2M hugepages=24576
node2 2M hugepages=24576
```

The basic CXL allocation smoke test passed and `/proc/PID/numa_maps` showed
the 1 GB hugepage mapping on node 2:

```text
file=/anon_hugepage (deleted) huge anon=512 dirty=512 N2=512 kernelpagesize_kB=2048
```

The required CXL idle-latency validation at 64 MB failed:

```text
file=/anon_hugepage (deleted) huge anon=32 dirty=32 N2=32 kernelpagesize_kB=2048
cycles_per_load=76.986
tsc_hz=1899974500
latency=40.5 ns
```

Per Phase 19 halt rule, CXL latency <100 ns stops the experiment before the
calibration matrix. A diagnostic 512 MB CXL run measured 783.520 cycles/load
at 1.899992 GHz, or 412.4 ns, but this does not release the pre-registered
halt on the required 64 MB validation.

---

## Phase 19.5 Current Platform Snapshot — Xeon Platinum 8592+
## Captured: 2026-05-14

Phase 19 diagnostics discovered that the current host is an Intel Xeon
Platinum 8592+ platform, not the original Phase 12 8462Y+ profile.

| Field | Value |
|-------|-------|
| CPU | INTEL(R) XEON(R) PLATINUM 8592+ |
| Family / Model / Stepping | 6 / 207 / 2 |
| Sockets | 2 |
| Physical cores per socket | 64 |
| SMT threads per core | 2 |
| Total logical CPUs | 256 |
| Node 0 CPUs | 0-63,128-191 |
| Node 1 CPUs | 64-127,192-255 |
| Node 2 CPUs | none; memory-only CXL node |
| L1d | 48 KB per core |
| L2 | 2 MB per core |
| L3 | 327,680 KiB per socket; 640 MiB total |
| CHA PMU tiles visible | 64 |
| CPU max MHz under setup | 1900 MHz |
| CXL node 2 memory | ~124,913 MB |

Current setup state:

```text
perf_event_paranoid=-1
numa_balancing=0
no_turbo=1
governor=performance
node0 2M hugepages=24576
node2 2M hugepages=24576
```

Architectural implication: the original 32 MB victim WSS is 53% of the
8462Y+ 60 MB LLC but only 10% of the 8592+ 320 MB LLC. Phase 19.5 scales
victim and aggressor parameters for this platform before interpreting
cross-placement results.
