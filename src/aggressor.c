/*
 * aggressor.c — Multi-threaded CXL streaming aggressor.
 *
 * Each thread reads through a per-thread region of CXL memory using
 * a configurable access mode. Supports optional bandwidth throttling
 * for iso-bandwidth experiments.
 *
 * Build:  gcc -O2 -march=native -mavx2 -msse4.1 -pthread -lnuma -o aggressor aggressor.c
 * Usage:  ./aggressor -m <mode> -t <nthreads> -c <corelist>
 *                     [-s <per_thread_MB>] [-d <seconds>]
 *                     [-R <throttle_MBps>]
 *
 * Modes: wb_load, wc_ntdqa, uc_load, wb_ntdqa, wb_prefetchnta
 */
#include "common.h"

/* ================================================================ */
/*                     Streaming Kernels                            */
/* ================================================================ */

/*
 * WB standard loads — hardware stream prefetcher activates.
 * Every fill enrolls in the Snoop Filter.
 */
static __attribute__((noinline))
uint64_t kern_wb_load(const char *buf, size_t sz)
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

/*
 * MOVNTDQA streaming loads — true non-temporal reads on WC memory;
 * on WB memory, implementation-defined (typically still cacheable).
 */
static __attribute__((noinline))
uint64_t kern_ntdqa(const char *buf, size_t sz)
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

/*
 * UC scalar loads — no caching, no combining, no prefetch.
 * Each 8-byte MOV is an independent MMIO-style transaction.
 */
static __attribute__((noinline))
uint64_t kern_uc_load(const char *buf, size_t sz)
{
    const char *end = buf + sz;

    for (const char *p = buf; p < end; p += CACHELINE) {
        asm volatile(
            "movq  0(%[addr]), %%rax   \n\t"
            "movq  8(%[addr]), %%rbx   \n\t"
            "movq 16(%[addr]), %%rcx   \n\t"
            "movq 24(%[addr]), %%rdx   \n\t"
            "movq 32(%[addr]), %%r8    \n\t"
            "movq 40(%[addr]), %%r9    \n\t"
            "movq 48(%[addr]), %%r10   \n\t"
            "movq 56(%[addr]), %%r11   \n\t"
            : : [addr] "r" (p)
            : "rax","rbx","rcx","rdx","r8","r9","r10","r11","memory");
    }
    return sz;
}

/*
 * PREFETCHNTA + normal loads on WB memory.
 * NTA hint still allocates Snoop-Filter entries on SPR.
 */
static __attribute__((noinline))
uint64_t kern_wb_nta(const char *buf, size_t sz)
{
    register __m256i s0 asm("ymm0") = _mm256_setzero_si256();
    register __m256i s1 asm("ymm1") = _mm256_setzero_si256();
    const char *end = buf + sz;
    const int ahead = 16;   /* prefetch distance: 16 cache lines */

    for (const char *p = buf; p < end; p += CACHELINE) {
        _mm_prefetch(p + ahead * CACHELINE, _MM_HINT_NTA);
        s0 = _mm256_xor_si256(s0, _mm256_load_si256((const __m256i *)(p)));
        s1 = _mm256_xor_si256(s1, _mm256_load_si256((const __m256i *)(p + 32)));
    }
    asm volatile("" :: "x"(s0), "x"(s1));
    return sz;
}

/* Dispatch table */
typedef uint64_t (*stream_fn)(const char *, size_t);
static stream_fn kernels[MODE_COUNT] = {
    [MODE_WB_LOAD]        = kern_wb_load,
    [MODE_WC_NTDQA]       = kern_ntdqa,
    [MODE_UC_LOAD]        = kern_uc_load,
    [MODE_WB_NTDQA]       = kern_ntdqa,     /* same kernel, different mapping */
    [MODE_WB_PREFETCHNTA] = kern_wb_nta,
};

/* ================================================================ */
/*                    Thread Configuration                          */
/* ================================================================ */

static volatile int g_ready = 0;

typedef struct {
    int            id;
    int            core;
    access_mode_t  mode;
    char          *buf;
    size_t         sz;
    uint64_t       throttle_mbps;   /* 0 = unthrottled */
    volatile int  *go;              /* shared start flag */
    volatile int  *stop;            /* shared stop flag  */
    /* results */
    uint64_t       total_bytes;
    double         elapsed_sec;
} tctx_t;

static void *thread_fn(void *arg)
{
    tctx_t *t = (tctx_t *)arg;
    pin_thread(t->core);

    stream_fn fn = kernels[t->mode];
    t->total_bytes = 0;

    /* Warm-up: 2 full passes to train prefetcher / populate TLB.
     * Use a simple watchdog to prevent infinite hangs on broken UC mappings. */
    uint64_t w_start = getns();
    for (int p = 0; p < 2; p++) {
        fn(t->buf, t->sz);
        if (getns() - w_start > 30000000000ULL) { /* 30s limit */
            fprintf(stderr, "[aggressor] thread %d: Warm-up TIMEOUT (stuck in UC?)\n", t->id);
            break;
        }
    }

    /* Signal ready */
    __atomic_fetch_add(&g_ready, 1, __ATOMIC_RELEASE);

    /* Spin until coordinator says go */
    while (!__atomic_load_n(t->go, __ATOMIC_ACQUIRE))
        _mm_pause();

    uint64_t t0 = getns();
    uint64_t throttle_bps = t->throttle_mbps * 1000000ULL;
    const size_t chunk = 64 * 1024;  /* 64 KB pacing granularity */
    uint64_t chunk_ns = 0;
    if (throttle_bps > 0)
        chunk_ns = (chunk * 1000000000ULL) / throttle_bps;

    while (!__atomic_load_n(t->stop, __ATOMIC_ACQUIRE)) {
        if (throttle_bps == 0) {
            /* Unthrottled: full-speed streaming pass */
            t->total_bytes += fn(t->buf, t->sz);
        } else {
            /* Throttled: pace each 64 KB chunk */
            for (size_t off = 0; off < t->sz; off += chunk) {
                if (__atomic_load_n(t->stop, __ATOMIC_ACQUIRE))
                    break;
                uint64_t cs = getns();
                size_t run = (off + chunk <= t->sz) ? chunk : (t->sz - off);
                fn(t->buf + off, run);
                t->total_bytes += run;
                /* busy-wait until pacing interval elapses */
                while (getns() - cs < chunk_ns)
                    _mm_pause();
            }
        }
    }

    uint64_t t1 = getns();
    t->elapsed_sec = (double)(t1 - t0) / 1e9;
    return NULL;
}

/* ================================================================ */
/*                           main()                                 */
/* ================================================================ */

static volatile sig_atomic_t g_stop = 0;
static void sighandler(int sig) { (void)sig; g_stop = 1; }

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s -m <mode> -t <nthreads> -c <corelist>\n"
        "         [-s <per_thread_MB>] [-d <seconds>] [-R <throttle_MBps>]\n"
        "\nModes: wb_load  wc_ntdqa  uc_load  wb_ntdqa  wb_prefetchnta\n"
        "\nExamples:\n"
        "  %s -m wb_load -t 16 -c 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16 -d 20\n"
        "  %s -m wc_ntdqa -t 8 -c 1,2,3,4,5,6,7,8 -d 20\n"
        "  %s -m wb_load -t 4 -c 1,2,3,4 -d 20 -R 800\n", prog, prog, prog, prog);
    exit(1);
}

int main(int argc, char **argv)
{
    access_mode_t  mode       = MODE_WB_LOAD;
    int            nthreads   = 1;
    int            cores[128];
    int            ncores     = 0;
    size_t         per_mb     = DEFAULT_BUF_MB;
    int            duration   = 15;
    uint64_t       throttle   = 0;
    int            opt;

    while ((opt = getopt(argc, argv, "m:t:c:s:d:R:h")) != -1) {
        switch (opt) {
        case 'm':
            {
                int found = 0;
                for (int i = 0; i < MODE_COUNT; i++) {
                    if (strcmp(optarg, mode_name[i]) == 0) { mode = i; found = 1; break; }
                }
                if (!found) { fprintf(stderr, "Unknown mode: %s\n", optarg); usage(argv[0]); }
            }
            break;
        case 't': nthreads  = atoi(optarg); break;
        case 'c': ncores    = parse_corelist(optarg, cores, 128); break;
        case 's': per_mb    = (size_t)atoi(optarg); break;
        case 'd': duration  = atoi(optarg); break;
        case 'R': throttle  = (uint64_t)atoi(optarg); break;
        default:  usage(argv[0]);
        }
    }
    if (nthreads < 1 || ncores < nthreads) usage(argv[0]);

    size_t per_sz   = per_mb * 1024UL * 1024;
    size_t total_sz = per_sz * (size_t)nthreads;

    fprintf(stderr, "[aggressor] mode=%s threads=%d bufsz=%zuMB/thread throttle=%lu MBps\n",
            mode_name[mode], nthreads, per_mb, throttle);

    /* ---- Allocate CXL buffer ------------------------------------ */
    char *base = NULL;
    switch (mode) {
    case MODE_WB_LOAD:
    case MODE_WB_NTDQA:
    case MODE_WB_PREFETCHNTA:
        base = (char *)alloc_wb_cxl(total_sz);
        break;
    case MODE_WC_NTDQA:
        base = (char *)map_cxl_device(DEV_CXL_WC, total_sz);
        break;
    case MODE_UC_LOAD:
        base = (char *)map_cxl_device(DEV_CXL_UC, total_sz);
        break;
    default:
        break;
    }
    if (!base) {
        fprintf(stderr, "[aggressor] FATAL: could not map CXL memory.\n");
        return 1;
    }
    fprintf(stderr, "[aggressor] mapped %zu MB CXL memory at %p\n",
            total_sz / (1024*1024), base);

    /* ---- Create threads ----------------------------------------- */
    volatile int go = 0;
    signal(SIGINT,  sighandler);
    signal(SIGTERM, sighandler);

    pthread_t  tids[128];
    tctx_t     ctx[128];

    g_ready = 0;
    for (int i = 0; i < nthreads; i++) {
        ctx[i] = (tctx_t){
            .id           = i,
            .core         = cores[i],
            .mode         = mode,
            .buf          = base + (size_t)i * per_sz,
            .sz           = per_sz,
            .throttle_mbps= throttle,
            .go           = &go,
            .stop         = &g_stop,
        };
        pthread_create(&tids[i], NULL, thread_fn, &ctx[i]);
    }

    /* Wait for all threads to be ready */
    while (__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE) < nthreads)
        _mm_pause();

    /* Small settle time, then go */
    usleep(10000);
    fprintf(stderr, "[aggressor] GO\n");
    __atomic_store_n(&go, 1, __ATOMIC_RELEASE);

    /* Run for requested duration */
    for (int s = 0; s < duration && !g_stop; s++)
        sleep(1);

    __atomic_store_n(&g_stop, 1, __ATOMIC_RELEASE);

    /* ---- Collect results ---------------------------------------- */
    double agg_bytes = 0, agg_time = 0;
    for (int i = 0; i < nthreads; i++) {
        pthread_join(tids[i], NULL);
        double bw = (double)ctx[i].total_bytes / ctx[i].elapsed_sec / 1e9;
        printf("thread %2d  core %2d  %.2f GB/s  (%.3f s)\n",
               i, ctx[i].core, bw, ctx[i].elapsed_sec);
        agg_bytes += (double)ctx[i].total_bytes;
        if (ctx[i].elapsed_sec > agg_time) agg_time = ctx[i].elapsed_sec;
    }
    double agg_bw = agg_bytes / agg_time / 1e9;
    printf("---\naggregate: %.2f GB/s  (%d threads, mode=%s)\n",
           agg_bw, nthreads, mode_name[mode]);

    /* Stdout machine-readable summary */
    printf("RESULT mode=%s threads=%d bw_gbps=%.3f throttle=%lu\n",
           mode_name[mode], nthreads, agg_bw, throttle);

    munmap(base, total_sz);
    return 0;
}
