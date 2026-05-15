/*
 * common.h — Shared definitions for coherence-trap experiments.
 *
 * Target: AMD EPYC (Zen 4 / Zen 4c) + Samsung CXL 2.0,
 *         or Intel Xeon (Sapphire Rapids). Edit L2_SIZE_BYTES and
 *         perf event codes for your CPU.
 *
 * Ubuntu 24.04 / kernel 6.17.
 */
#ifndef COMMON_H
#define COMMON_H

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <time.h>
#include <math.h>
#include <pthread.h>
#include <sched.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <linux/perf_event.h>
#include <numa.h>
#include <numaif.h>
#include <immintrin.h>

/* ------------------------------------------------------------------ */
/*  System topology — AMD EPYC + Samsung CXL 2.0                     */
/*                                                                    */
/*  Run `numactl --hardware` to check NUMA distances.                 */
/*  LOCAL_NUMA_NODE: fallback only; prefer explicit runtime -n flags. */
/*  AGG_NUMA_NODE:   where aggressors run (closest to CXL).           */
/*  On this system node 1 has shorter distance to CXL node 2.         */
/* ------------------------------------------------------------------ */
#define CXL_NUMA_NODE    2
#define LOCAL_NUMA_NODE  0
#define AGG_NUMA_NODE    1
#define CACHELINE        64
#define PAGE_4K          4096
#define PAGE_2M          (2UL * 1024 * 1024)

/*
 * L2 cache size per core — MUST match your actual CPU.
 * Run:  getconf LEVEL2_CACHE_SIZE   or   lscpu | grep L2
 *
 * Zen 4c/Zen 4 values vary by SKU/stepping; trust runtime probing.
 * Common values observed:         524288 (512 KB), 1048576 (1 MB)
 * Intel SPR (32C):                2097152  (2 MB)
 *
 * FILL THIS IN after Step 0 (scripts/step0_characterize.sh).
 */
#ifndef L2_SIZE_BYTES
#define L2_SIZE_BYTES  1048576  /* Default only; override per host after step0 characterization. */
#endif

/*
 * Victim working set: 3 arrays must fit in ~75% of L2.
 * Each array = VICTIM_ARRAY_KB kilobytes.
 * Total = 3 * VICTIM_ARRAY_KB * 1024 bytes.
 */
#define VICTIM_ARRAY_KB  ((L2_SIZE_BYTES * 3 / 4) / 1024 / 3)
/* Bergamo 512KB L2 → VICTIM_ARRAY_KB = 128 → total 384 KB (75% of L2) */
/* Genoa   1MB L2   → VICTIM_ARRAY_KB = 256 → total 768 KB (75% of L2) */

#define DEFAULT_BUF_MB   256
#define DEFAULT_BUF_SZ   (DEFAULT_BUF_MB * 1024UL * 1024)

#define DEV_CXL_UC       "/dev/cxl_uc"
#define DEV_CXL_WC       "/dev/cxl_wc"

/* ------------------------------------------------------------------ */
/*  Perf raw event codes — AMD Zen 4 Family 19h                       */
/*                                                                    */
/*  Encoding: config = event[7:0] | (umask[7:0] << 8)                */
/*  Verify:   perf stat -e rXXXX -a sleep 1                          */
/*            perf list | grep -i l2                                  */
/*                                                                    */
/*  For Intel SPR use: RAW_L2_HIT=0x02D1, RAW_L2_MISS=0x10D1         */
/* ------------------------------------------------------------------ */

/*
 * L2 Cache Accesses from DC Misses (PMCx064):
 *   umask 0x70 = L2 Hit from DC Miss (all L2 hit sub-events)
 *   umask 0x08 = L2 Miss from DC Miss
 */
#define RAW_L2_HIT_FROM_DC_MISS   0x7064   /* event=0x64, umask=0x70 */
#define RAW_L2_MISS_FROM_DC_MISS  0x0864   /* event=0x64, umask=0x08 */

#define RAW_L2_HIT    RAW_L2_HIT_FROM_DC_MISS
#define RAW_L2_MISS   RAW_L2_MISS_FROM_DC_MISS

/* ------------------------------------------------------------------ */
/*  Access modes                                                      */
/* ------------------------------------------------------------------ */
typedef enum {
    MODE_WB_LOAD = 0,
    MODE_WC_NTDQA,
    MODE_UC_LOAD,
    MODE_WB_NTDQA,
    MODE_WB_PREFETCHNTA,
    MODE_COUNT
} access_mode_t;

static const char *mode_name[] __attribute__((unused)) = {
    [MODE_WB_LOAD]       = "wb_load",
    [MODE_WC_NTDQA]      = "wc_ntdqa",
    [MODE_UC_LOAD]       = "uc_load",
    [MODE_WB_NTDQA]      = "wb_ntdqa",
    [MODE_WB_PREFETCHNTA]= "wb_prefetchnta",
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */
static inline uint64_t rdtsc_fenced(void)
{
    unsigned lo, hi;
    asm volatile("lfence; rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

static inline uint64_t getns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static inline void pin_thread(int core)
{
    cpu_set_t cs;
    CPU_ZERO(&cs);
    CPU_SET(core, &cs);
    if (sched_setaffinity(0, sizeof(cs), &cs) < 0) {
        fprintf(stderr, "pin_thread(%d): %s\n", core, strerror(errno));
        exit(1);
    }
}

static inline int parse_corelist(const char *s, int *out, int max)
{
    int n = 0;
    char *dup = strdup(s), *tok;
    for (tok = strtok(dup, ","); tok && n < max; tok = strtok(NULL, ","))
        out[n++] = atoi(tok);
    free(dup);
    return n;
}

static inline void *alloc_wb_cxl(size_t sz)
{
    /* Use mbind BEFORE population to avoid migration overhead.
     * We drop MAP_POPULATE and let memset/first-touch handle it. */
    void *p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB,
                   -1, 0);
    if (p == MAP_FAILED)
        p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
                 MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) { perror("mmap WB"); return NULL; }

    unsigned long mask = 1UL << CXL_NUMA_NODE;
    if (mbind(p, sz, MPOL_BIND, &mask,
              sizeof(mask) * 8, MPOL_MF_STRICT) < 0) {
        perror("mbind CXL node");
        munmap(p, sz);
        return NULL;
    }
    /* First-touch population happens here, directly on the CXL node. */
    memset(p, 0xAB, sz);

    /* Spot-check placement (every 64 MB to avoid slowdown) */
    for (size_t off = 0; off < sz; off += 64 * 1024 * 1024) {
        void *page = (char *)p + off;
        int actual = -1;
        if (move_pages(0, 1, &page, NULL, &actual, 0) == 0 &&
            actual != CXL_NUMA_NODE) {
            fprintf(stderr, "FATAL: page at +%zu on node %d, expected %d\n",
                    off, actual, CXL_NUMA_NODE);
            munmap(p, sz);
            return NULL;
        }
    }
    return p;
}

/* Map CXL memory via kernel-module char device (UC or WC). Do not use
 * MAP_POPULATE for UC/WC — those mappings may not support normal write-back. */
static inline void *map_cxl_device(const char *devpath, size_t sz)
{
    int fd = open(devpath, O_RDWR);
    if (fd < 0) { perror(devpath); return NULL; }

    void *p = mmap(NULL, sz, PROT_READ | PROT_WRITE,
                   MAP_SHARED, fd, 0);
    close(fd);
    if (p == MAP_FAILED) { perror("mmap device"); return NULL; }

    /* DO NOT memset — UC/WC pages may not support write-back caching.
       For read experiments, pre-populate from kernel side or accept
       that content is zero/undefined. */
    return p;
}

static inline int perf_open(uint32_t type, uint64_t config, int group)
{
    struct perf_event_attr pe = {
        .type           = type,
        .size           = sizeof(pe),
        .config         = config,
        .disabled       = (group == -1) ? 1 : 0,
        .exclude_kernel = 1,
        .exclude_hv     = 1,
    };
    int fd = (int)syscall(__NR_perf_event_open, &pe, 0, -1, group, 0);
    if (fd < 0)
        fprintf(stderr, "perf_event_open(type=%u,cfg=0x%lx): %s\n",
                type, (unsigned long)config, strerror(errno));
    return fd;
}

static inline uint64_t perf_read(int fd)
{
    uint64_t v = 0;
    if (read(fd, &v, sizeof(v)) != sizeof(v))
        perror("perf read");
    return v;
}

#endif /* COMMON_H */
