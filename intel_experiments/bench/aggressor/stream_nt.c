/*
 * stream_nt.c — WB region with MOVNTDQA loads, hardware prefetcher ON (Condition D)
 *
 * This is structurally identical to stream_wb.c (WB page, prefetcher on),
 * but the inner loop uses MOVNTDQA instead of regular loads.
 * Per Intel ORM §7.5.3: NT hint on WB pages is advisory; hardware may
 * treat it as a regular cacheable load or honor the NT bypass.
 *
 * If D ≈ A: NT hint is ignored → same SF pressure (H4 supported).
 * If D ≈ C: NT hint is honored → no SF entries created (H4 rejected).
 *
 * The prefetcher remains ON because MOVNTDQA on WB pages still triggers
 * the hardware prefetcher in most Intel implementations.
 *
 * Usage:
 *   ./stream_nt --cpu 1 --node 0 --region-gb 1 --duration-sec 60
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
#include <immintrin.h>

#include "../lib/timing.h"
#include "../lib/hugepage.h"
#include "../lib/msr.h"

#define DEFAULT_REGION_GB 1
#define DEFAULT_DURATION  60.0
#define REPORT_INTERVAL   1.0

static volatile sig_atomic_t g_stop = 0;
static void handle_sigterm(int sig) { (void)sig; g_stop = 1; }

static void pin_to_cpu(int cpu)
{
    cpu_set_t cs;
    CPU_ZERO(&cs); CPU_SET(cpu, &cs);
    if (sched_setaffinity(0, sizeof(cs), &cs) < 0) { perror("sched_setaffinity"); exit(1); }
}

static uint64_t stream_nt_read_once(const char *buf, size_t size)
{
    const __m128i *p   = (const __m128i *)buf;
    const __m128i *end = (const __m128i *)(buf + size);
    volatile __m128i sink;
    while (p < end) {
        sink = _mm_stream_load_si128((__m128i *)p + 0);
        sink = _mm_stream_load_si128((__m128i *)p + 1);
        sink = _mm_stream_load_si128((__m128i *)p + 2);
        sink = _mm_stream_load_si128((__m128i *)p + 3);
        p += 4;
    }
    _mm_sfence();
    (void)sink;
    return size;
}

int main(int argc, char **argv)
{
    int    cpu         = 1;
    int    node        = 0;
    size_t region_gb   = DEFAULT_REGION_GB;
    double duration_sec = DEFAULT_DURATION;
    int    verify_msr  = 1;

    static struct option opts[] = {
        {"cpu",         required_argument, 0, 'c'},
        {"node",        required_argument, 0, 'n'},
        {"region-gb",   required_argument, 0, 'r'},
        {"duration-sec",required_argument, 0, 'd'},
        {"no-verify",   no_argument,       0, 'v'},
        {0, 0, 0, 0}
    };
    int opt, idx;
    while ((opt = getopt_long(argc, argv, "c:n:r:d:v", opts, &idx)) != -1) {
        switch (opt) {
            case 'c': cpu        = atoi(optarg); break;
            case 'n': node       = atoi(optarg); break;
            case 'r': region_gb  = (size_t)atol(optarg); break;
            case 'd': duration_sec = atof(optarg); break;
            case 'v': verify_msr = 0; break;
            default: exit(1);
        }
    }

    signal(SIGTERM, handle_sigterm);
    signal(SIGINT,  handle_sigterm);

    pin_to_cpu(cpu);

    /* Verify prefetcher is ON */
    if (verify_msr) {
        uint64_t msr_val = 0;
        if (msr_read(cpu, 0x1A4, &msr_val) == 0 && msr_val != 0) {
            fprintf(stderr, "stream_nt: WARNING: MSR 0x1A4 = 0x%lx on cpu%d "
                    "(expected 0x0 for condition D)\n", msr_val, cpu);
        }
    }

    size_t region_size = region_gb * 1024UL * 1024UL * 1024UL;
    void *buf = hugepage_alloc(region_size, node);
    if (buf == MAP_FAILED) exit(1);
    if (((uintptr_t)buf & 15) != 0) {
        fprintf(stderr, "stream_nt: ERROR: buffer not 16-byte aligned\n");
        exit(1);
    }
    memset(buf, 0xAB, region_size);

    fprintf(stderr, "stream_nt (MOVNTDQA on WB, pf=ON): cpu=%d node=%d "
            "region=%zu GB duration=%.0f s\n",
            cpu, node, region_gb, duration_sec);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    double deadline = t0.tv_sec + t0.tv_nsec * 1e-9 + duration_sec;

    uint64_t total_bytes = 0;
    int iteration = 0;
    double last_report = t0.tv_sec + t0.tv_nsec * 1e-9;

    while (!g_stop) {
        uint64_t swept = stream_nt_read_once((const char *)buf, region_size);
        total_bytes += swept;
        iteration++;

        clock_gettime(CLOCK_MONOTONIC, &t1);
        double now = t1.tv_sec + t1.tv_nsec * 1e-9;

        if (now - last_report >= REPORT_INTERVAL) {
            double bw_gbps = (double)(swept) / (now - last_report) / 1e9;
            fprintf(stderr, "stream_nt [cpu%d]: iter=%d bw=%.2f GB/s\n",
                    cpu, iteration, bw_gbps);
            last_report = now;
        }

        if (now >= deadline) break;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    double avg_bw = (double)total_bytes / elapsed / 1e9;

    printf("{\"cpu\": %d, \"condition\": \"D_nt_wb\", \"region_gb\": %zu, "
           "\"iterations\": %d, \"total_bytes\": %lu, "
           "\"elapsed_sec\": %.3f, \"avg_bw_gbps\": %.3f}\n",
           cpu, region_gb, iteration, total_bytes, elapsed, avg_bw);

    hugepage_free(buf, region_size);
    return 0;
}
