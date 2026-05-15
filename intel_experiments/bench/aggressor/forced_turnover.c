/*
 * forced_turnover.c — Forced L2 line-turnover aggressor (Phase 18)
 *
 * Allocates a 4 MB hugepage region per core (2× L2 capacity on SPR) and
 * performs continuous random-index loads to force L2 line eviction and
 * sustained new-line SF allocations.
 *
 * Unlike random_chase (Phase 17), which uses a Sattolo-cycle over 1.5 MB
 * (fully L2-resident, near-zero new SF allocations in steady state), this
 * aggressor:
 *   - Uses 4 MB WSS (> 2 MB L2) to force ~50% L2 miss rate per core
 *   - Issues independent random-index loads (not a pointer chain) to allow
 *     OoO execution to sustain maximum concurrent L2 misses simultaneously
 *   - Disables all four hardware prefetchers via MSR 0x1A4 before any access
 *     to prevent prefetcher pre-loading from distorting SF allocation rate
 *
 * Predicted SF allocation rate: ~16 M new L2 fills/sec per core.
 * At 32 cores: ~500 M/sec against ~2 M SF entries → SF saturation regime.
 *
 * Loops until SIGTERM; restores MSR and reports iteration count on exit.
 * One iteration = BATCH_SIZE (64) independent random loads.
 *
 * Usage:
 *   ./forced_turnover --cpu N --node 0 --region-bytes 4194304 --seed S
 */

#define _GNU_SOURCE
#include <sys/mman.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <getopt.h>
#include <sched.h>
#include <signal.h>

#include "../lib/timing.h"
#include "../lib/hugepage.h"
#include "../lib/msr.h"

#define CACHELINE_SIZE     64
#define DEFAULT_REGION     (4 * 1024 * 1024UL)   /* 4 MB: 2× L2 capacity */
#define DEFAULT_SEED       42
#define BATCH_SIZE         64   /* random-index loads per counted iteration */

static volatile sig_atomic_t g_stop     = 0;
static uint64_t              g_pf_saved = (uint64_t)-1;
static int                   g_cpu      = 0;

static void handle_sigterm(int sig) { (void)sig; g_stop = 1; }

/*
 * Force a single random-index load that the compiler cannot eliminate.
 * "m" constraint on buf[idx] forces a real memory read instruction.
 * Independent calls with different idx values allow OoO to issue concurrent
 * L2-miss loads, maximising new SF entry allocation rate per unit time.
 */
#define LOAD1(buf, idx) \
    __asm__ volatile ("movq %0, %%rax\n\t" \
                      : : "m"((buf)[idx]) : "rax", "memory")

static void pin_to_cpu(int cpu)
{
    cpu_set_t cs;
    CPU_ZERO(&cs);
    CPU_SET(cpu, &cs);
    if (sched_setaffinity(0, sizeof(cs), &cs) < 0) {
        perror("sched_setaffinity");
        exit(1);
    }
}

int main(int argc, char **argv)
{
    int          cpu          = 1;
    int          node         = 0;
    size_t       region_bytes = DEFAULT_REGION;
    unsigned int seed         = DEFAULT_SEED;

    static struct option opts[] = {
        {"cpu",          required_argument, 0, 'c'},
        {"node",         required_argument, 0, 'n'},
        {"region-bytes", required_argument, 0, 'r'},
        {"seed",         required_argument, 0, 's'},
        {0, 0, 0, 0}
    };

    int opt, idx;
    while ((opt = getopt_long(argc, argv, "c:n:r:s:", opts, &idx)) != -1) {
        switch (opt) {
            case 'c': cpu          = atoi(optarg);         break;
            case 'n': node         = atoi(optarg);         break;
            case 'r': region_bytes = (size_t)atol(optarg); break;
            case 's': seed         = (unsigned)atoi(optarg); break;
            default:
                fprintf(stderr,
                        "usage: forced_turnover --cpu N --node N "
                        "--region-bytes N --seed N\n");
                exit(1);
        }
    }

    g_cpu = cpu;

    /* Round up to 2 MB hugepage boundary */
    const size_t HP  = 2 * 1024 * 1024;
    size_t region_size = (region_bytes + HP - 1) & ~(HP - 1);

    signal(SIGTERM, handle_sigterm);
    signal(SIGINT,  handle_sigterm);

    pin_to_cpu(cpu);

    /* Disable all four hardware prefetchers on this core (MSR 0x1A4 bits[3:0]).
     * Prefetchers would pre-load lines into L2 ahead of demand, inflating
     * L2 hit rate and suppressing SF new-entry allocation pressure. */
    g_pf_saved = msr_pf_disable(cpu, 0x0F);
    if (g_pf_saved == (uint64_t)-1) {
        fprintf(stderr,
                "forced_turnover: WARNING: prefetcher disable failed on cpu%d "
                "(requires root + /dev/cpu/%d/msr)\n", cpu, cpu);
    }

    void *buf_ptr = hugepage_alloc(region_size, node);
    if (buf_ptr == MAP_FAILED) {
        fprintf(stderr, "forced_turnover: hugepage_alloc failed (size=%zu)\n",
                region_size);
        msr_pf_restore(cpu, g_pf_saved);
        exit(1);
    }

    /* Touch all pages to ensure physical allocation and TLB population */
    memset(buf_ptr, 0, region_size);

    size_t n_qwords = region_size / sizeof(uint64_t);
    volatile uint64_t *buf = (volatile uint64_t *)buf_ptr;

    fprintf(stderr,
            "forced_turnover: cpu=%d node=%d region=%zu KB n_qwords=%zu "
            "seed=%u pf_disabled=%d batch=%d\n",
            cpu, node, region_size >> 10, n_qwords, seed,
            (g_pf_saved != (uint64_t)-1), BATCH_SIZE);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    uint64_t     iterations  = 0;
    unsigned int local_seed  = seed + (unsigned int)cpu;

    while (!g_stop) {
        /* BATCH_SIZE independent random-index loads per iteration.
         * OoO execution can issue all BATCH_SIZE in flight simultaneously,
         * maximising concurrent L2 misses and SF allocation rate. */
        for (int i = 0; i < BATCH_SIZE; i++) {
            size_t j = (size_t)rand_r(&local_seed) % n_qwords;
            LOAD1(buf, j);
        }
        iterations++;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;

    double loads_per_sec = (elapsed > 0)
        ? (double)iterations * BATCH_SIZE / elapsed : 0.0;
    double bw_gbps       = loads_per_sec * CACHELINE_SIZE / 1e9;
    double iter_per_sec  = (elapsed > 0) ? (double)iterations / elapsed : 0.0;

    /* Restore prefetcher MSR before writing output */
    msr_pf_restore(cpu, g_pf_saved);

    printf("{\"cpu\": %d, \"condition\": \"R_forced_turnover\", "
           "\"region_bytes\": %zu, \"n_qwords\": %zu, "
           "\"iterations\": %lu, \"loads_per_iter\": %d, "
           "\"elapsed_sec\": %.3f, \"iter_per_sec\": %.1f, "
           "\"approx_bw_gbps\": %.3f}\n",
           cpu, region_size, n_qwords,
           (unsigned long)iterations, BATCH_SIZE,
           elapsed, iter_per_sec, bw_gbps);

    hugepage_free(buf_ptr, region_size);
    return 0;
}
