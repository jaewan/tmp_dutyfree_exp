/*
 * validate_memtype.c — Verify UC/WC/WB mappings actually differ.
 *
 * Expected results on CXL (~200–300 ns base latency):
 *   WB sequential:  ~5–15 GB/s per thread (prefetcher active)
 *   WC sequential:  ~1–5 GB/s (WCB-limited, no prefetch)
 *   UC sequential:  ~0.05–0.3 GB/s (serialized, no caching)
 *   WB latency:     ~150–300 ns (L3 miss to CXL)
 *   UC latency:     ~200–400 ns (uncached round-trip)
 *
 * If UC ≈ WB bandwidth: mapping is broken (PAT aliasing). Stop and offline memory.
 *
 * Build:  make bin/validate
 * Usage:  ./bin/validate -c <core>
 */
#include "common.h"

/* Bandwidth: sequential read, 4 MB region */
static uint64_t bw_wb_load(const char *buf, size_t sz)
{
    __m256i s = _mm256_setzero_si256();
    for (const char *p = buf; p < buf + sz; p += CACHELINE)
        s = _mm256_xor_si256(s, _mm256_load_si256((const __m256i *)p));
    asm volatile("" :: "x"(s));
    return sz;
}

static uint64_t bw_ntdqa(const char *buf, size_t sz)
{
    __m256i s = _mm256_setzero_si256();
    for (const char *p = buf; p < buf + sz; p += CACHELINE)
        s = _mm256_xor_si256(s, _mm256_stream_load_si256((__m256i *)p));
    asm volatile("" :: "x"(s));
    return sz;
}

static uint64_t bw_uc_scalar(const char *buf, size_t sz)
{
    uint64_t sink = 0;
    for (const char *p = buf; p < buf + sz; p += CACHELINE) {
        uint64_t v;
        asm volatile("movq (%1), %0" : "=r"(v) : "r"(p) : "memory");
        sink += v;
    }
    asm volatile("" :: "r"(sink));
    return sz;
}

/* Latency: pointer chase over 2 MB, measure per-hop ns */
typedef struct node { struct node *next; char pad[56]; } node_t;

static double measure_latency(void *buf, size_t sz)
{
    size_t n = sz / sizeof(node_t);
    node_t *nodes = (node_t *)buf;

    for (size_t i = 0; i < n - 1; i++)
        nodes[i].next = &nodes[i + 1];
    nodes[n - 1].next = &nodes[0];
    _mm_sfence(); /* order WC stores before chase (no-op on WB) */

    node_t *p = &nodes[0];
    for (size_t i = 0; i < n * 2; i++) p = p->next;

    size_t steps = n * 4;
    uint64_t t0 = rdtsc_fenced();
    for (size_t i = 0; i < steps; i++)
        p = p->next;
    uint64_t t1 = rdtsc_fenced();
    asm volatile("" :: "r"(p));

    uint64_t ns0 = getns();
    uint64_t c0 = rdtsc_fenced();
    usleep(10000);
    uint64_t c1 = rdtsc_fenced();
    uint64_t ns1 = getns();
    double tsc_ghz = (double)(c1 - c0) / (double)(ns1 - ns0);

    return (double)(t1 - t0) / (double)steps / tsc_ghz;
}

static void run_bw_test(const char *label, const char *buf, size_t sz,
                        uint64_t (*fn)(const char *, size_t))
{
    fn(buf, sz);
    fn(buf, sz);

    uint64_t t0 = getns();
    uint64_t total = 0;
    int passes = 0;
    while (getns() - t0 < 2000000000ULL) {
        total += fn(buf, sz);
        passes++;
    }
    uint64_t elapsed = getns() - t0;
    double gbps = (double)total / (double)elapsed;
    printf("  %-25s  %8.3f GB/s  (%d passes)\n", label, gbps, passes);
}

int main(int argc, char **argv)
{
    int core = 0;
    int opt;
    while ((opt = getopt(argc, argv, "c:")) != -1) {
        if (opt == 'c') core = atoi(optarg);
    }
    pin_thread(core);

    size_t sz = 4 * 1024 * 1024;

    printf("=== Memory Type Validation (core %d) ===\n\n", core);

    printf("[WB] CXL NUMA node %d, normal mapping:\n", CXL_NUMA_NODE);
    void *wb = alloc_wb_cxl(sz);
    if (wb) {
        run_bw_test("WB + AVX2 load", wb, sz, bw_wb_load);
        run_bw_test("WB + MOVNTDQA", wb, sz, bw_ntdqa);
        run_bw_test("WB + scalar (1 load/CL)", wb, sz, bw_uc_scalar);
        double lat = measure_latency(wb, sz);
        printf("  %-25s  %8.1f ns/hop\n", "WB sequential latency", lat);
        munmap(wb, sz);
    } else {
        printf("  FAILED to allocate WB CXL memory\n");
    }
    printf("\n");

    printf("[WC] /dev/cxl_wc mapping:\n");
    void *wc = map_cxl_device(DEV_CXL_WC, sz);
    if (wc) {
        run_bw_test("WC + MOVNTDQA", wc, sz, bw_ntdqa);
        run_bw_test("WC + scalar (1 load/CL)", wc, sz, bw_uc_scalar);
        double lat = measure_latency(wc, sz);
        printf("  %-25s  %8.1f ns/hop\n", "WC sequential latency", lat);
        munmap(wc, sz);
    } else {
        printf("  FAILED — is cxl_memtype loaded? Memory offlined?\n");
    }
    printf("\n");

    printf("[UC] /dev/cxl_uc mapping:\n");
    void *uc = map_cxl_device(DEV_CXL_UC, sz);
    if (uc) {
        run_bw_test("UC + scalar (1 load/CL)", uc, sz, bw_uc_scalar);
        double lat = measure_latency(uc, sz);
        printf("  %-25s  %8.1f ns/hop\n", "UC sequential latency", lat);
        munmap(uc, sz);
    } else {
        printf("  FAILED — is cxl_memtype loaded? Memory offlined?\n");
    }

    printf("\n=== PASS/FAIL Criteria ===\n");
    printf("  WB BW >> UC BW  (expect 10x+ difference)\n");
    printf("  UC latency ~ WB latency (both see CXL RTT)\n");
    printf("  If UC BW ~ WB BW: mapping broken, PAT aliasing likely.\n");

    return 0;
}
