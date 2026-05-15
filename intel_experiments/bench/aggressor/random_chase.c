/*
 * random_chase.c — Persistent random-access pointer-chase aggressor (Phase 17)
 *
 * Creates a randomized circular linked list within a 1.5 MB hugepage region and
 * chases it continuously to maximize SF entry persistence. Each core pins ~1.5 MB
 * of L2-resident data with random access → maximum SF occupancy per core.
 *
 * Uses Sattolo's algorithm to create a single cycle of length n, guaranteeing
 * the full working set is traversed before any line is revisited.
 *
 * Loops until SIGTERM; reports completed iterations on exit.
 *
 * Usage:
 *   ./random_chase --cpu N --node 0 --region-bytes 1572864 --seed S [--sf-bypass]
 *
 * --sf-bypass: After each pointer dereference, call CLDEMOTE on the source line.
 *   This releases the SF entry for the accessed line, approximating STREAMING's
 *   H2 directory-bypass clause. Used for the S32 condition in Phase 17.
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

#define CACHELINE_SIZE    64
#define DEFAULT_REGION    1572864UL  /* 1.5 MB */
#define DEFAULT_SEED      42

static volatile sig_atomic_t g_stop = 0;
static void handle_sigterm(int sig) { (void)sig; g_stop = 1; }

/*
 * Normal pointer chase — 8 loads per call, each load address-dependent on
 * the previous. The "+r" constraint ensures the compiler treats p as both
 * input and output, preventing elimination of the entire load chain.
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

/*
 * SF-bypass pointer chase — load pointer, then CLDEMOTE the source line.
 * CLDEMOTE demotes the source line from L2 to L3, releasing its SF entry.
 * rax is used as scratch for the old address; listed as clobbered.
 *
 * Sequence per step:
 *   rax = p (save source address)
 *   p   = *p (follow pointer, p now holds next address)
 *   cldemote [rax] (demote source line to L3, releasing SF entry)
 */
#define CHASE1_CLDEMOTE(p) \
    __asm__ volatile ( \
        "mov %0, %%rax\n\t"     \
        "mov (%0), %0\n\t"      \
        "cldemote (%%rax)\n\t"  \
        : "+r"(p) : : "rax", "memory" )

#define CHASE8_CLDEMOTE(p) do { \
    CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); \
    CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); CHASE1_CLDEMOTE(p); \
} while(0)

/*
 * Sattolo's algorithm — builds a permutation that is a SINGLE cycle of
 * length n. Unlike Fisher-Yates (which produces multiple cycles), Sattolo
 * guarantees the pointer chase visits every node before returning to start.
 *
 * Algorithm: for i in [n-1, n-2, ..., 1]: swap(arr[i], arr[rand % i])
 * Note j < i (not j <= i), which prevents fixed points and ensures single cycle.
 */
static void build_sattolo_list(void *buf, size_t size, unsigned int seed)
{
    size_t n = size / CACHELINE_SIZE;
    uintptr_t *ptrs = malloc(n * sizeof(uintptr_t));
    if (!ptrs) { perror("malloc"); exit(1); }

    for (size_t i = 0; i < n; i++)
        ptrs[i] = (uintptr_t)buf + i * CACHELINE_SIZE;

    for (size_t i = n - 1; i > 0; i--) {
        size_t j = (size_t)rand_r(&seed) % i;   /* j in [0, i-1] — Sattolo */
        uintptr_t tmp = ptrs[i]; ptrs[i] = ptrs[j]; ptrs[j] = tmp;
    }

    /* Write circular next-pointer into the first 8 bytes of each cache line */
    for (size_t i = 0; i < n; i++)
        *(uintptr_t *)ptrs[i] = ptrs[(i + 1) % n];

    free(ptrs);
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
    int          cpu         = 1;
    int          node        = 0;
    size_t       region_bytes = DEFAULT_REGION;
    unsigned int seed        = DEFAULT_SEED;
    int          sf_bypass   = 0;

    static struct option opts[] = {
        {"cpu",          required_argument, 0, 'c'},
        {"node",         required_argument, 0, 'n'},
        {"region-bytes", required_argument, 0, 'r'},
        {"seed",         required_argument, 0, 's'},
        {"sf-bypass",    no_argument,       0, 'b'},
        {0, 0, 0, 0}
    };

    int opt, idx;
    while ((opt = getopt_long(argc, argv, "c:n:r:s:b", opts, &idx)) != -1) {
        switch (opt) {
            case 'c': cpu          = atoi(optarg); break;
            case 'n': node         = atoi(optarg); break;
            case 'r': region_bytes = (size_t)atol(optarg); break;
            case 's': seed         = (unsigned int)atoi(optarg); break;
            case 'b': sf_bypass    = 1; break;
            default: fprintf(stderr, "usage: random_chase [options]\n"); exit(1);
        }
    }

    /* Round up to 2 MB hugepage boundary */
    const size_t HP = 2 * 1024 * 1024;
    size_t region_size = (region_bytes + HP - 1) & ~(HP - 1);

    signal(SIGTERM, handle_sigterm);
    signal(SIGINT,  handle_sigterm);

    pin_to_cpu(cpu);

    void *buf = hugepage_alloc(region_size, node);
    if (buf == MAP_FAILED) {
        fprintf(stderr, "random_chase: hugepage_alloc failed (size=%zu)\n", region_size);
        exit(1);
    }

    /* Touch all pages; build the Sattolo circular linked list */
    memset(buf, 0, region_size);
    build_sattolo_list(buf, region_size, seed + (unsigned int)cpu);

    size_t n_lines = region_size / CACHELINE_SIZE;

    fprintf(stderr, "random_chase: cpu=%d node=%d region=%zu KB n_lines=%zu "
                    "seed=%u sf_bypass=%d\n",
            cpu, node, region_size >> 10, n_lines, seed, sf_bypass);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    uint64_t iterations = 0;
    uintptr_t p = *(uintptr_t *)buf;   /* start of Sattolo cycle */

    while (!g_stop) {
        /* Chase through all n_lines nodes (one full cycle) */
        for (size_t i = 0; i < n_lines; i += 8) {
            if (sf_bypass) {
                CHASE8_CLDEMOTE(p);
            } else {
                CHASE8(p);
            }
        }
        iterations++;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    double iter_per_sec = (elapsed > 0) ? (double)iterations / elapsed : 0.0;

    /* Lines touched per second = iterations × n_lines */
    double lines_per_sec = iter_per_sec * (double)n_lines;
    double bw_gbps = lines_per_sec * CACHELINE_SIZE / 1e9;

    printf("{\"cpu\": %d, \"condition\": \"%s\", \"region_bytes\": %zu, "
           "\"n_lines\": %zu, \"iterations\": %lu, "
           "\"elapsed_sec\": %.3f, \"iter_per_sec\": %.1f, "
           "\"approx_bw_gbps\": %.3f}\n",
           cpu,
           sf_bypass ? "S_cldemote" : "R_random_chase",
           region_size, n_lines,
           (unsigned long)iterations,
           elapsed, iter_per_sec, bw_gbps);

    hugepage_free(buf, region_size);
    return 0;
}
