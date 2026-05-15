/*
 * pointer_chase.c — Cache-line-dependent random walk (victim benchmark)
 *
 * Builds a randomized linked list within a WSS-byte hugepage region.
 * Measures cycles-per-dependent-load using rdtscp serialization.
 * Emits one JSON object per trial to stdout.
 *
 * Usage:
 *   ./pointer_chase --cpu 0 --node 0 --wss 33554432 --trials 30
 *                   --run-sec 1.0 [--pf-disable]
 *
 * The --pf-disable flag writes MSR 0x1A4 = 0xF on the victim core.
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

#include "../lib/timing.h"
#include "../lib/hugepage.h"
#include "../lib/msr.h"

#define CACHELINE_SIZE 64
#define DEFAULT_WSS    (32 * 1024 * 1024)  /* 32 MB */
#define DEFAULT_TRIALS 30
#define DEFAULT_SEC    1.0

/* Build a random permutation of cache-line-aligned pointers covering [buf, buf+size).
 * Each element stores the ADDRESS of the next element as a uintptr_t.
 * Uses Fisher-Yates shuffle. */
static void build_random_list(void *buf, size_t size)
{
    size_t n = size / CACHELINE_SIZE;
    uintptr_t *ptrs = malloc(n * sizeof(uintptr_t));
    if (!ptrs) { perror("malloc"); exit(1); }

    for (size_t i = 0; i < n; i++)
        ptrs[i] = (uintptr_t)buf + i * CACHELINE_SIZE;

    /* Fisher-Yates in-place shuffle */
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = (size_t)rand() % (i + 1);
        uintptr_t tmp = ptrs[i]; ptrs[i] = ptrs[j]; ptrs[j] = tmp;
    }

    /* Each cache line's first 8 bytes = address of the next cache line */
    for (size_t i = 0; i < n; i++)
        *(uintptr_t *)ptrs[i] = ptrs[(i + 1) % n];

    free(ptrs);
}

/*
 * Dependent load chain using inline assembly.
 * "mov (%0), %0" loads the 64-bit value at the address in %0 back into %0.
 * Each load's result is the address for the NEXT load — a true RAW dependency.
 * The compiler cannot reorder or eliminate these: the "+r" constraint marks
 * p as both input and output, making the reads visible to the compiler.
 * Unrolled 8× per macro call; called 4× = 32 loads per loop iteration.
 */
#define CHASE8(p) \
    __asm__ volatile ( \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        "mov (%0), %0\n\t" \
        : "+r"(p) : : "memory" )

/* Run one pointer-chase trial for duration_sec seconds.
 * Returns (total TSC ticks, total loads). */
static void run_trial(void *head, double duration_sec,
                      uint64_t *out_ticks, uint64_t *out_loads,
                      uint64_t tsc_hz)
{
    uint64_t target_ticks = (uint64_t)(duration_sec * (double)tsc_hz);
    uintptr_t p = (uintptr_t)head;
    uint64_t loads = 0;

    /* Warm up: one pass of 1024 chained loads to populate caches */
    for (size_t i = 0; i < 128; i++)
        CHASE8(p);

    uint64_t t0 = rdtscp_start();
    uint64_t deadline = t0 + target_ticks;

    while (1) {
        CHASE8(p);
        CHASE8(p);
        CHASE8(p);
        CHASE8(p);
        loads += 32;
        if (rdtscp_end() >= deadline) break;
    }

    uint64_t t1 = rdtscp_end();
    *out_ticks = t1 - t0;
    *out_loads = loads;
}

/* Estimate TSC frequency by comparing TSC ticks to clock_gettime over ~100 ms */
static uint64_t estimate_tsc_hz(void)
{
    struct timespec ts0, ts1;
    uint64_t t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &ts0);
    t0 = rdtscp_start();
    usleep(100000);
    t1 = rdtscp_end();
    clock_gettime(CLOCK_MONOTONIC, &ts1);
    double elapsed = (ts1.tv_sec - ts0.tv_sec) +
                     (ts1.tv_nsec - ts0.tv_nsec) * 1e-9;
    return (uint64_t)((t1 - t0) / elapsed);
}

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
    int    cpu       = 0;
    int    node      = 0;
    size_t wss       = DEFAULT_WSS;
    int    trials    = DEFAULT_TRIALS;
    double run_sec   = DEFAULT_SEC;
    int    pf_disable = 0;

    static struct option opts[] = {
        {"cpu",        required_argument, 0, 'c'},
        {"node",       required_argument, 0, 'n'},
        {"wss",        required_argument, 0, 'w'},
        {"trials",     required_argument, 0, 't'},
        {"run-sec",    required_argument, 0, 's'},
        {"pf-disable", no_argument,       0, 'p'},
        {0, 0, 0, 0}
    };

    int opt, idx;
    while ((opt = getopt_long(argc, argv, "c:n:w:t:s:p", opts, &idx)) != -1) {
        switch (opt) {
            case 'c': cpu       = atoi(optarg); break;
            case 'n': node      = atoi(optarg); break;
            case 'w': wss       = (size_t)atol(optarg); break;
            case 't': trials    = atoi(optarg); break;
            case 's': run_sec   = atof(optarg); break;
            case 'p': pf_disable = 1; break;
            default: fprintf(stderr, "usage: pointer_chase [options]\n"); exit(1);
        }
    }

    /* Align WSS to 2MB hugepage boundary */
    const size_t HP = 2 * 1024 * 1024;
    wss = (wss + HP - 1) & ~(HP - 1);

    pin_to_cpu(cpu);
    srand(42);

    fprintf(stderr, "victim: cpu=%d node=%d wss=%zu MB trials=%d "
                    "run_sec=%.1f pf_disable=%d\n",
            cpu, node, wss >> 20, trials, run_sec, pf_disable);

    void *buf = hugepage_alloc(wss, node);
    if (buf == MAP_FAILED) exit(1);

    uint64_t saved_msr = (uint64_t)-1;
    if (pf_disable) {
        saved_msr = msr_pf_disable(cpu, 0xF);
        if (saved_msr == (uint64_t)-1) {
            fprintf(stderr, "victim: MSR write failed — aborting\n");
            exit(1);
        }
        fprintf(stderr, "victim: MSR 0x1A4 disabled (saved=0x%lx)\n", saved_msr);
    }

    uint64_t tsc_hz = estimate_tsc_hz();
    fprintf(stderr, "victim: TSC frequency estimate: %lu Hz (%.2f GHz)\n",
            tsc_hz, tsc_hz * 1e-9);

    /* Print JSON array header */
    printf("[\n");

    for (int trial = 0; trial < trials; trial++) {
        /* Re-randomize list layout each trial (anti-alias per X6 in METHODOLOGY) */
        srand(42 + trial);
        build_random_list(buf, wss);

        uint64_t ticks = 0, loads = 0;
        run_trial(buf, run_sec, &ticks, &loads, tsc_hz);

        double cycles_per_load = (loads > 0) ? (double)ticks / (double)loads : 0.0;
        double elapsed_sec     = (double)ticks / (double)tsc_hz;

        printf("  {\"trial\": %d, \"cycles_per_load\": %.3f, "
               "\"total_loads\": %lu, \"ticks\": %lu, "
               "\"elapsed_sec\": %.4f, \"tsc_hz\": %lu}%s\n",
               trial, cycles_per_load, loads, ticks, elapsed_sec, tsc_hz,
               (trial < trials - 1) ? "," : "");

        fflush(stdout);
        usleep(50000);
    }

    printf("]\n");

    if (saved_msr != (uint64_t)-1)
        msr_pf_restore(cpu, saved_msr);

    hugepage_free(buf, wss);
    return 0;
}
