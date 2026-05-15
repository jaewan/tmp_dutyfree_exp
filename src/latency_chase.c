/*
 * latency_chase.c -- Random pointer chase for CXL round-trip latency.
 *
 * Uses a Fisher-Yates shuffled linked list over a region larger than
 * all caches to force every hop through the memory hierarchy to CXL
 * (or local DRAM for baseline).
 *
 * Modes:
 *   wb   - WB mapping on CXL node (measures L3-miss-to-CXL RTT)
 *   dram - WB mapping on local DRAM (baseline, measures L3-miss-to-DRAM RTT)
 *
 * Build: make bin/latency_chase
 * Usage: ./bin/latency_chase -m wb   -c 1
 *        ./bin/latency_chase -m dram -c 1 [-n <dram_node>]
 */
#include "common.h"

#define DEFAULT_REGION_MB  64

typedef struct cnode { struct cnode *next; char pad[56]; } cnode_t;

static cnode_t *build_random_chase(void *buf, size_t sz)
{
    size_t n = sz / sizeof(cnode_t);
    cnode_t *nodes = (cnode_t *)buf;
    size_t *perm = malloc(n * sizeof(size_t));
    if (!perm) { perror("malloc perm"); exit(1); }

    for (size_t i = 0; i < n; i++) perm[i] = i;

    unsigned seed = 0xdeadbeef;
    for (size_t i = n - 1; i > 0; i--) {
        seed = seed * 1664525u + 1013904223u;
        size_t j = seed % (i + 1);
        size_t t = perm[i]; perm[i] = perm[j]; perm[j] = t;
    }
    for (size_t i = 0; i < n - 1; i++)
        nodes[perm[i]].next = &nodes[perm[i + 1]];
    nodes[perm[n - 1]].next = &nodes[perm[0]];

    cnode_t *head = &nodes[perm[0]];
    free(perm);
    return head;
}

int main(int argc, char **argv)
{
    const char *mode = "wb";
    int core = 1;
    int region_mb = DEFAULT_REGION_MB;
    int dram_node = -1;
    int opt;

    while ((opt = getopt(argc, argv, "m:c:r:n:")) != -1) {
        switch (opt) {
        case 'm': mode = optarg; break;
        case 'c': core = atoi(optarg); break;
        case 'r': region_mb = atoi(optarg); break;
        case 'n': dram_node = atoi(optarg); break;
        default:
            fprintf(stderr, "Usage: %s -m <wb|dram> -c <core> [-r <region_MB>] [-n <dram_node>]\n", argv[0]);
            return 1;
        }
    }
    pin_thread(core);

    size_t sz = (size_t)region_mb * 1024 * 1024;
    void *buf = NULL;

    if (strcmp(mode, "wb") == 0) {
        buf = alloc_wb_cxl(sz);
    } else if (strcmp(mode, "dram") == 0) {
        if (dram_node < 0) {
            int node_from_core = numa_node_of_cpu(core);
            dram_node = (node_from_core >= 0) ? node_from_core : LOCAL_NUMA_NODE;
        }
        buf = mmap(NULL, sz, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (buf != MAP_FAILED) {
            unsigned long mask = 1UL << dram_node;
            if (mbind(buf, sz, MPOL_BIND, &mask, sizeof(mask) * 8,
                      MPOL_MF_STRICT) < 0) {
                perror("mbind dram");
                munmap(buf, sz);
                return 1;
            }
            memset(buf, 0, sz);
        }
    } else {
        fprintf(stderr, "Unknown mode '%s'. Use 'wb' or 'dram'.\n", mode);
        return 1;
    }

    if (!buf || buf == MAP_FAILED) {
        fprintf(stderr, "Failed to allocate %d MB in mode '%s'\n", region_mb, mode);
        return 1;
    }

    fprintf(stderr, "[latency_chase] mode=%s core=%d region=%dMB dram_node=%d (%zu nodes)\n",
            mode, core, region_mb, dram_node, sz / sizeof(cnode_t));

    cnode_t *head = build_random_chase(buf, sz);

    size_t n = sz / sizeof(cnode_t);

    /* Warmup: two full traversals to populate TLBs */
    cnode_t *p = head;
    for (size_t i = 0; i < n * 2; i++)
        p = p->next;
    asm volatile("" :: "r"(p));

    /* Calibrate TSC frequency */
    uint64_t c0 = rdtsc_fenced();
    uint64_t n0 = getns();
    usleep(50000);
    uint64_t c1 = rdtsc_fenced();
    uint64_t n1 = getns();
    double tsc_ghz = (double)(c1 - c0) / (double)(n1 - n0);
    fprintf(stderr, "[latency_chase] TSC freq: %.3f GHz\n", tsc_ghz);

    /* Timed chase */
    size_t steps = n * 4;
    if (steps < (1 << 22)) steps = (1 << 22);

    p = head;
    uint64_t t0 = rdtsc_fenced();
    for (size_t i = 0; i < steps; i++) {
        p = p->next;
    }
    uint64_t t1 = rdtsc_fenced();
    asm volatile("" :: "r"(p));

    double cycles_per_hop = (double)(t1 - t0) / (double)steps;
    double ns_per_hop = cycles_per_hop / tsc_ghz;

    printf("LATENCY mode=%-6s core=%d region=%dMB steps=%zu "
           "cycles_per_hop=%.1f ns_per_hop=%.1f\n",
           mode, core, region_mb, steps, cycles_per_hop, ns_per_hop);

    munmap(buf, sz);
    return 0;
}
