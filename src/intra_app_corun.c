#include "common.h"

typedef enum {
    AGG_NONE = 0,
    AGG_WB_LOAD,
    AGG_WC_NTDQA,
    AGG_WB_COLUMN_SCAN,
    AGG_MODE_COUNT
} agg_mode_t;

static const char *agg_mode_name[AGG_MODE_COUNT] = {
    [AGG_NONE] = "none",
    [AGG_WB_LOAD] = "wb_load",
    [AGG_WC_NTDQA] = "wc_ntdqa",
    [AGG_WB_COLUMN_SCAN] = "wb_column_scan",
};

typedef struct pnode { struct pnode *next; char pad[56]; } pnode_t;

static pnode_t *build_chase_seeded(void *base, size_t sz, unsigned seed)
{
    size_t n = sz / sizeof(pnode_t);
    pnode_t *nodes = (pnode_t *)base;
    size_t *idx = malloc(n * sizeof(size_t));
    if (!idx) {
        fprintf(stderr, "malloc idx failed\n");
        return NULL;
    }

    for (size_t i = 0; i < n; i++) idx[i] = i;
    for (size_t i = n - 1; i > 0; i--) {
        size_t j = (size_t)(rand_r(&seed) % (i + 1));
        size_t tmp = idx[i]; idx[i] = idx[j]; idx[j] = tmp;
    }
    for (size_t i = 0; i < n - 1; i++) nodes[idx[i]].next = &nodes[idx[i + 1]];
    nodes[idx[n - 1]].next = &nodes[idx[0]];

    size_t first = idx[0];
    free(idx);
    return &nodes[first];
}

static __attribute__((noinline)) uint64_t chase_pass(pnode_t *head, size_t steps)
{
    pnode_t *p = head;
    for (size_t i = 0; i < steps; i++) p = p->next;
    asm volatile("" :: "r"(p));
    return steps;
}

static __attribute__((noinline)) uint64_t kern_wb_load(const char *buf, size_t sz)
{
    register __m256i s0 asm("ymm0") = _mm256_setzero_si256();
    register __m256i s1 asm("ymm1") = _mm256_setzero_si256();
    const char *end = buf + sz;

    for (const char *p = buf; p < end; p += CACHELINE) {
        s0 = _mm256_xor_si256(s0, _mm256_load_si256((const __m256i *)(p)));
        s1 = _mm256_xor_si256(s1, _mm256_load_si256((const __m256i *)(p + 32)));
    }
    asm volatile("" :: "x"(s0), "x"(s1));
    return sz;
}

static __attribute__((noinline)) uint64_t kern_ntdqa(const char *buf, size_t sz)
{
    __m256i s0 = _mm256_setzero_si256();
    __m256i s1 = _mm256_setzero_si256();
    const char *end = buf + sz;

    for (const char *p = buf; p < end; p += CACHELINE) {
        s0 = _mm256_xor_si256(s0, _mm256_stream_load_si256((__m256i *)(p)));
        s1 = _mm256_xor_si256(s1, _mm256_stream_load_si256((__m256i *)(p + 32)));
    }
    asm volatile("" :: "x"(s0), "x"(s1));
    return sz;
}

static __attribute__((noinline)) uint64_t kern_wb_column_scan(const char *buf, size_t sz)
{
    size_t n = sz / 16;
    const uint64_t *col_pred = (const uint64_t *)buf;
    const uint64_t *col_proj = (const uint64_t *)(buf + n * sizeof(uint64_t));
    uint64_t acc = 0;

    for (size_t i = 0; i < n; i++) {
        uint64_t v = col_pred[i];
        if ((v & 0x3F) < 8) acc += col_proj[i];
    }
    asm volatile("" :: "r"(acc));
    return n * sizeof(uint64_t) * 2;
}

typedef struct {
    int core;
    pnode_t *head;
    size_t steps;
    volatile int *phase;
    uint64_t iters;
    uint64_t cycles;
    uint64_t insns;
    uint64_t l2_hit;
    uint64_t l2_miss;
    double elapsed_sec;
} victim_ctx_t;

typedef struct {
    int id;
    int core;
    agg_mode_t mode;
    char *buf;
    size_t sz;
    volatile int *phase;
    uint64_t total_bytes;
    double elapsed_sec;
} agg_ctx_t;

static void *victim_thread_fn(void *arg)
{
    victim_ctx_t *v = (victim_ctx_t *)arg;
    pin_thread(v->core);

    int fd_cyc = perf_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_CPU_CYCLES, -1);
    int fd_ins = perf_open(PERF_TYPE_HARDWARE, PERF_COUNT_HW_INSTRUCTIONS, fd_cyc);
    int fd_l2h = perf_open(PERF_TYPE_RAW, RAW_L2_HIT, fd_cyc);
    int fd_l2m = perf_open(PERF_TYPE_RAW, RAW_L2_MISS, fd_cyc);

    if (fd_cyc < 0 || fd_ins < 0 || fd_l2h < 0 || fd_l2m < 0) {
        fprintf(stderr, "[intra] victim perf setup failed\n");
        exit(1);
    }

    while (__atomic_load_n(v->phase, __ATOMIC_ACQUIRE) == 0) {
        chase_pass(v->head, v->steps);
    }

    ioctl(fd_cyc, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP);
    ioctl(fd_cyc, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP);
    uint64_t t0 = getns();

    while (__atomic_load_n(v->phase, __ATOMIC_ACQUIRE) == 1) {
        chase_pass(v->head, v->steps);
        v->iters++;
    }

    uint64_t t1 = getns();
    ioctl(fd_cyc, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP);

    v->cycles = perf_read(fd_cyc);
    v->insns = perf_read(fd_ins);
    v->l2_hit = perf_read(fd_l2h);
    v->l2_miss = perf_read(fd_l2m);
    v->elapsed_sec = (double)(t1 - t0) / 1e9;

    close(fd_cyc);
    close(fd_ins);
    close(fd_l2h);
    close(fd_l2m);
    return NULL;
}

static uint64_t agg_pass(agg_mode_t mode, const char *buf, size_t sz)
{
    if (mode == AGG_WB_LOAD) return kern_wb_load(buf, sz);
    if (mode == AGG_WC_NTDQA) return kern_ntdqa(buf, sz);
    if (mode == AGG_WB_COLUMN_SCAN) return kern_wb_column_scan(buf, sz);
    return 0;
}

static void *agg_thread_fn(void *arg)
{
    agg_ctx_t *a = (agg_ctx_t *)arg;
    pin_thread(a->core);

    while (__atomic_load_n(a->phase, __ATOMIC_ACQUIRE) == 0) {
        agg_pass(a->mode, a->buf, a->sz);
    }

    uint64_t t0 = getns();
    while (__atomic_load_n(a->phase, __ATOMIC_ACQUIRE) == 1) {
        a->total_bytes += agg_pass(a->mode, a->buf, a->sz);
    }
    uint64_t t1 = getns();
    a->elapsed_sec = (double)(t1 - t0) / 1e9;
    return NULL;
}

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s -c <victim_core> -n <victim_node> -v <victim_ws_kb> -W <warmup_s> -d <measure_s> -S <seed>\n"
        "          -m <none|wb_load|wc_ntdqa|wb_column_scan> [-t <agg_threads> -a <agg_corelist> -s <agg_mb_per_thread>]\n",
        prog);
    exit(1);
}

int main(int argc, char **argv)
{
    int victim_core = 128;
    int victim_node = 1;
    size_t victim_ws_kb = 4096;
    int warmup = 5;
    int measure = 15;
    unsigned seed = 1;
    agg_mode_t mode = AGG_NONE;
    int nthreads = 0;
    int agg_cores[128] = {0};
    int ncores = 0;
    size_t per_mb = 256;

    int opt;
    while ((opt = getopt(argc, argv, "c:n:v:W:d:S:m:t:a:s:h")) != -1) {
        switch (opt) {
        case 'c': victim_core = atoi(optarg); break;
        case 'n': victim_node = atoi(optarg); break;
        case 'v': victim_ws_kb = (size_t)atoi(optarg); break;
        case 'W': warmup = atoi(optarg); break;
        case 'd': measure = atoi(optarg); break;
        case 'S': seed = (unsigned)atoi(optarg); break;
        case 'm':
            if (strcmp(optarg, "none") == 0) mode = AGG_NONE;
            else if (strcmp(optarg, "wb_load") == 0) mode = AGG_WB_LOAD;
            else if (strcmp(optarg, "wc_ntdqa") == 0) mode = AGG_WC_NTDQA;
            else if (strcmp(optarg, "wb_column_scan") == 0) mode = AGG_WB_COLUMN_SCAN;
            else usage(argv[0]);
            break;
        case 't': nthreads = atoi(optarg); break;
        case 'a': ncores = parse_corelist(optarg, agg_cores, 128); break;
        case 's': per_mb = (size_t)atoi(optarg); break;
        default: usage(argv[0]);
        }
    }

    if (mode != AGG_NONE && (nthreads < 1 || ncores < nthreads)) usage(argv[0]);

    fprintf(stderr,
        "[intra] victim_core=%d victim_node=%d ws_kb=%zu warmup=%d measure=%d seed=%u mode=%s agg_threads=%d per_mb=%zu\n",
        victim_core, victim_node, victim_ws_kb, warmup, measure, seed, agg_mode_name[mode], nthreads, per_mb);

    size_t ws_bytes = victim_ws_kb * 1024;
    void *victim_mem = mmap(NULL, ws_bytes, PROT_READ | PROT_WRITE,
                            MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
    if (victim_mem == MAP_FAILED) {
        perror("mmap victim");
        return 1;
    }

    unsigned long mask = 1UL << victim_node;
    if (mbind(victim_mem, ws_bytes, MPOL_BIND, &mask, sizeof(mask) * 8,
              MPOL_MF_STRICT | MPOL_MF_MOVE) < 0) {
        fprintf(stderr, "mbind victim failed node=%d: %s\n", victim_node, strerror(errno));
        return 1;
    }

    pnode_t *head = build_chase_seeded(victim_mem, ws_bytes, seed ^ 0x9E3779B9u);
    if (!head) return 1;
    size_t chase_steps = ws_bytes / sizeof(pnode_t);

    char *agg_base = NULL;
    size_t per_sz = per_mb * 1024UL * 1024;
    size_t total_agg_sz = per_sz * (size_t)nthreads;

    if (mode == AGG_WB_LOAD || mode == AGG_WB_COLUMN_SCAN) {
        agg_base = (char *)alloc_wb_cxl(total_agg_sz);
        if (!agg_base) return 1;
    } else if (mode == AGG_WC_NTDQA) {
        agg_base = (char *)map_cxl_device(DEV_CXL_WC, total_agg_sz);
        if (!agg_base) return 1;
    }

    volatile int phase = 0; /* 0 warmup, 1 measure, 2 stop */

    victim_ctx_t vctx = {
        .core = victim_core,
        .head = head,
        .steps = chase_steps,
        .phase = &phase,
        .iters = 0,
    };

    pthread_t vtid;
    if (pthread_create(&vtid, NULL, victim_thread_fn, &vctx) != 0) {
        fprintf(stderr, "pthread_create victim failed\n");
        return 1;
    }

    pthread_t atids[128];
    agg_ctx_t actx[128];
    for (int i = 0; i < nthreads; i++) {
        actx[i] = (agg_ctx_t){
            .id = i,
            .core = agg_cores[i],
            .mode = mode,
            .buf = agg_base + (size_t)i * per_sz,
            .sz = per_sz,
            .phase = &phase,
        };
        if (pthread_create(&atids[i], NULL, agg_thread_fn, &actx[i]) != 0) {
            fprintf(stderr, "pthread_create aggressor %d failed\n", i);
            return 1;
        }
    }

    sleep(warmup);
    __atomic_store_n(&phase, 1, __ATOMIC_RELEASE);
    sleep(measure);
    __atomic_store_n(&phase, 2, __ATOMIC_RELEASE);

    pthread_join(vtid, NULL);

    double agg_bytes = 0.0;
    double agg_time = 0.0;
    for (int i = 0; i < nthreads; i++) {
        pthread_join(atids[i], NULL);
        agg_bytes += (double)actx[i].total_bytes;
        if (actx[i].elapsed_sec > agg_time) agg_time = actx[i].elapsed_sec;
    }

    double bw = 0.0;
    if (nthreads > 0 && agg_time > 0.0) bw = agg_bytes / agg_time / 1e9;

    double ipc = (vctx.cycles > 0) ? (double)vctx.insns / (double)vctx.cycles : 0.0;
    double miss_rate = (vctx.l2_hit + vctx.l2_miss > 0)
        ? 100.0 * (double)vctx.l2_miss / (double)(vctx.l2_hit + vctx.l2_miss)
        : 0.0;
    double cpi = (vctx.iters > 0) ? (double)vctx.cycles / (double)vctx.iters : 0.0;

    printf("VICTIM core=%d ipc=%.4f l2_miss_rate=%.2f cycles=%lu insns=%lu l2_hit=%lu l2_miss=%lu iters=%lu sec=%.2f cycles_per_iter=%.6f mode=pointer_chase\n",
           victim_core, ipc, miss_rate, vctx.cycles, vctx.insns, vctx.l2_hit, vctx.l2_miss, vctx.iters, vctx.elapsed_sec, cpi);
    printf("AGGRESSOR mode=%s threads=%d bw_gbps=%.3f sec=%.2f bytes=%.0f\n",
           agg_mode_name[mode], nthreads, bw, agg_time, agg_bytes);
    printf("RESULT mode=%s threads=%d bw_gbps=%.3f victim_cycles_per_iter=%.6f seed=%u\n",
           agg_mode_name[mode], nthreads, bw, cpi, seed);

    if (agg_base) munmap(agg_base, total_agg_sz);
    munmap(victim_mem, ws_bytes);
    return 0;
}
