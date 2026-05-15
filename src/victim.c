/*
 * victim.c — L2-cache-resident victim workload with self-monitoring.
 *
 * Runs a STREAM-triad kernel over three arrays that fit in the
 * private L2 cache (~1.125 MB total on SPR's 2 MB L2).
 * Reports IPC and L2 miss rate via perf_event_open.
 *
 * Build:  gcc -O2 -march=native -mavx2 -pthread -lnuma -lm -o victim victim.c
 * Usage:  ./victim -c <core> [-n <numa_node>] [-w <ws_KB>] [-d <seconds>] [-W <warmup_sec>]
 *
 * The victim runs for <warmup> seconds (unreported), then measures
 * for <duration> seconds and prints results on exit.
 */
#include "common.h"

/* Default: 3 arrays × VICTIM_ARRAY_KB — fits in ~75% of L2 (set in common.h) */
#define DEFAULT_WS_KB  VICTIM_ARRAY_KB
#define REPORT_INTERVAL_SEC  1

/* ================================================================ */
/*  Victim Workload: STREAM Triad — a[i] = b[i] + scalar * c[i]    */
/* ================================================================ */
static __attribute__((noinline))
uint64_t triad_pass(double * __restrict a,
                    const double * __restrict b,
                    const double * __restrict c,
                    size_t n, double scalar)
{
    for (size_t i = 0; i < n; i++)
        a[i] = b[i] + scalar * c[i];
    return n * sizeof(double) * 3;   /* bytes touched */
}

/* ================================================================ */
/*  Pointer-chase victim (alternative: latency-bound workload)      */
/*  Uncomment to use instead of STREAM triad for pure latency test. */
/* ================================================================ */
typedef struct pnode { struct pnode *next; char pad[56]; } pnode_t;

static pnode_t *build_chase(void *base, size_t sz)
{
    size_t n = sz / sizeof(pnode_t);
    pnode_t *nodes = (pnode_t *)base;
    /* Fisher–Yates shuffle → random Hamiltonian cycle */
    size_t *idx = malloc(n * sizeof(size_t));
    for (size_t i = 0; i < n; i++) idx[i] = i;
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = (size_t)rand() % (i + 1);
        size_t tmp = idx[i]; idx[i] = idx[j]; idx[j] = tmp;
    }
    for (size_t i = 0; i < n - 1; i++)
        nodes[idx[i]].next = &nodes[idx[i + 1]];
    nodes[idx[n - 1]].next = &nodes[idx[0]];
    size_t first = idx[0];
    free(idx);
    return &nodes[first];
}

static __attribute__((noinline))
uint64_t chase_pass(pnode_t *head, size_t steps)
{
    pnode_t *p = head;
    for (size_t i = 0; i < steps; i++)
        p = p->next;
    /* sink to prevent optimization */
    asm volatile("" :: "r"(p));
    return steps;
}

/* ================================================================ */
/*  main                                                            */
/* ================================================================ */

static volatile sig_atomic_t g_stop = 0;
static void sighandler(int s) { (void)s; g_stop = 1; }

static void usage(const char *p)
{
    fprintf(stderr,
        "Usage: %s -c <core> [-n <numa_node>] [-w <ws_KB>] [-d <sec>] [-W <warmup>]\n"
        "         [-P]   (use pointer-chase instead of STREAM triad)\n", p);
    exit(1);
}

int main(int argc, char **argv)
{
    int    core      = 0;
    int    local_node= LOCAL_NUMA_NODE;
    size_t ws_kb     = DEFAULT_WS_KB;
    int    duration  = 20;
    int    warmup    = 5;
    int    use_chase = 0;
    int    opt;

    while ((opt = getopt(argc, argv, "c:n:w:d:W:Ph")) != -1) {
        switch (opt) {
        case 'c': core     = atoi(optarg); break;
        case 'n': local_node = atoi(optarg); break;
        case 'w': ws_kb    = (size_t)atoi(optarg); break;
        case 'd': duration = atoi(optarg); break;
        case 'W': warmup   = atoi(optarg); break;
        case 'P': use_chase= 1; break;
        default:  usage(argv[0]);
        }
    }
    pin_thread(core);
    signal(SIGINT,  sighandler);
    signal(SIGTERM, sighandler);

    fprintf(stderr, "[victim] core=%d node=%d ws=%zu KB duration=%d warmup=%d chase=%d\n",
            core, local_node, ws_kb, duration, warmup, use_chase);

    /* ---- Allocate working set on LOCAL DRAM ---------------------- */
    size_t ws_bytes = ws_kb * 1024;
    size_t arr_n    = ws_bytes / sizeof(double);
    size_t alloc_sz = ws_bytes * 3 + PAGE_4K;  /* a, b, c */

    void *raw = mmap(NULL, alloc_sz, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (raw == MAP_FAILED) { perror("mmap victim"); return 1; }

    unsigned long mask = 1UL << local_node;
    if (mbind(raw, alloc_sz, MPOL_BIND, &mask, sizeof(mask) * 8,
              MPOL_MF_STRICT | MPOL_MF_MOVE) < 0) {
        fprintf(stderr, "[victim] FATAL: mbind to node %d failed: %s\n",
                local_node, strerror(errno));
        munmap(raw, alloc_sz);
        return 1;
    }

    double *a = (double *)raw;
    double *b = a + arr_n;
    double *c = b + arr_n;
    double scalar = 3.14159265358979;

    /* Initialize */
    for (size_t i = 0; i < arr_n; i++) {
        a[i] = 0.0;
        b[i] = (double)(i & 0xFF);
        c[i] = (double)((i * 7) & 0xFF);
    }

    /* Pointer-chase setup (if requested) */
    pnode_t *chase_head = NULL;
    size_t chase_steps  = 0;
    if (use_chase) {
        chase_head = build_chase(raw, ws_bytes);
        chase_steps = ws_bytes / sizeof(pnode_t);
    }

    /* ---- Open perf counters ------------------------------------- */
    int fd_cyc   = perf_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, -1);
    int fd_ins   = perf_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, fd_cyc);
    int fd_l2h   = perf_open(PERF_TYPE_RAW, RAW_L2_HIT,  fd_cyc);
    int fd_l2m   = perf_open(PERF_TYPE_RAW, RAW_L2_MISS, fd_cyc);

    if (fd_cyc < 0 || fd_ins < 0 || fd_l2h < 0 || fd_l2m < 0) {
        fprintf(stderr, "[victim] FATAL: cannot open perf events "
                "(try: echo 1 > /proc/sys/kernel/perf_event_paranoid)\n");
        return 1;
    }

    /* ---- Warm-up phase ------------------------------------------ */
    fprintf(stderr, "[victim] warming up for %d s …\n", warmup);
    uint64_t w0 = getns();
    while ((getns() - w0) < (uint64_t)warmup * 1000000000ULL && !g_stop) {
        if (use_chase)
            chase_pass(chase_head, chase_steps);
        else
            triad_pass(a, b, c, arr_n, scalar);
    }

    /* ---- Measurement phase -------------------------------------- */
    fprintf(stderr, "[victim] measuring for %d s …\n", duration);

    /* Reset & enable counters */
    ioctl(fd_cyc, PERF_EVENT_IOC_RESET,  PERF_IOC_FLAG_GROUP);
    ioctl(fd_cyc, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);

    uint64_t m0 = getns();
    uint64_t iters = 0;
    uint64_t next_report = m0 + 1000000000ULL;

    while (!g_stop) {
        uint64_t now = getns();
        if (now - m0 >= (uint64_t)duration * 1000000000ULL)
            break;

        if (use_chase)
            chase_pass(chase_head, chase_steps);
        else
            triad_pass(a, b, c, arr_n, scalar);
        iters++;

        /* Periodic interim report (read snapshot; do NOT reset — final output is cumulative) */
        if (now >= next_report) {
            uint64_t cyc_snap = perf_read(fd_cyc);
            uint64_t ins_snap = perf_read(fd_ins);
            uint64_t l2h_snap = perf_read(fd_l2h);
            uint64_t l2m_snap = perf_read(fd_l2m);
            double ipc_snap   = (cyc_snap > 0) ? (double)ins_snap / (double)cyc_snap : 0;
            double miss_snap  = (l2h_snap + l2m_snap > 0) ? 100.0 * l2m_snap / (l2h_snap + l2m_snap) : 0;
            fprintf(stderr, "[victim] interim: IPC=%.3f  L2miss_rate=%.2f%%  iters=%lu\n", ipc_snap, miss_snap, iters);
            next_report = now + 1000000000ULL;
        }
    }

    ioctl(fd_cyc, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);

    /* ---- Final readout ------------------------------------------ */
    uint64_t cyc = perf_read(fd_cyc);
    uint64_t ins = perf_read(fd_ins);
    uint64_t l2h = perf_read(fd_l2h);
    uint64_t l2m = perf_read(fd_l2m);

    double meas_sec = (double)(getns() - m0) / 1e9;
    double ipc      = (cyc > 0) ? (double)ins / (double)cyc : 0;
    double miss_rate= (l2h + l2m > 0) ? 100.0 * l2m / (l2h + l2m) : 0;

    printf("VICTIM core=%d ipc=%.4f l2_miss_rate=%.2f cycles=%lu insns=%lu "
           "l2_hit=%lu l2_miss=%lu iters=%lu sec=%.2f\n",
           core, ipc, miss_rate, cyc, ins, l2h, l2m, iters, meas_sec);
    fprintf(stderr, "[victim] DONE: IPC=%.4f  L2_miss=%.2f%%\n",
            ipc, miss_rate);

    close(fd_cyc); close(fd_ins); close(fd_l2h); close(fd_l2m);
    munmap(raw, alloc_sz);
    return 0;
}
