/*
 * stream_wb.c — WB streaming aggressor, hardware prefetcher ON (Condition A)
 *
 * Requires _GNU_SOURCE for CPU affinity macros.
 *
 * Maps a 1 GB region as anonymous WB memory (default), reads it sequentially
 * in 64-byte cache-line strides. Hardware prefetchers are active (MSR 0x1A4 = 0x0).
 * Reports achieved bandwidth in GB/s as a JSON stream.
 *
 * Usage:
 *   ./stream_wb --cpu 1 --node 0 --region-gb 1 --duration-sec 60
 *
 * The process loops continuously until killed; designed to run in background
 * while the victim pointer_chase runs in the foreground.
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

#define DEFAULT_REGION_GB 1
#define DEFAULT_DURATION  60.0
#define REPORT_INTERVAL   1.0   /* report BW every N seconds */

static volatile sig_atomic_t g_stop = 0;
static void handle_sigterm(int sig) { (void)sig; g_stop = 1; }

static void pin_to_cpu(int cpu)
{
    cpu_set_t cs;
    CPU_ZERO(&cs); CPU_SET(cpu, &cs);
    if (sched_setaffinity(0, sizeof(cs), &cs) < 0) { perror("sched_setaffinity"); exit(1); }
}

/* Sequential 64B read sweep, returns bytes read */
static uint64_t stream_read_once(const char *buf, size_t size)
{
    const char *p = buf;
    const char *end = buf + size;
    volatile uint64_t sink = 0;
    while (p < end) {
        sink ^= *(volatile const uint64_t *)p;
        p += 64;
    }
    (void)sink;
    return size;
}

int main(int argc, char **argv)
{
    int    cpu         = 1;
    int    node        = 0;
    size_t region_gb   = DEFAULT_REGION_GB;
    double duration_sec = DEFAULT_DURATION;
    int    verify_msr  = 1;   /* check that MSR 0x1A4 == 0 (prefetcher on) */

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

    /* Verify prefetcher state: MSR 0x1A4 must be 0 (all enabled) */
    if (verify_msr) {
        uint64_t msr_val = 0;
        if (msr_read(cpu, 0x1A4, &msr_val) < 0) {
            fprintf(stderr, "stream_wb: WARNING: cannot read MSR 0x1A4 on cpu%d "
                    "(run sudo setup.sh)\n", cpu);
        } else if (msr_val != 0) {
            fprintf(stderr, "stream_wb: ERROR: MSR 0x1A4 cpu%d = 0x%lx "
                    "(expected 0x0 for condition A — prefetcher ON)\n", cpu, msr_val);
            fprintf(stderr, "  If running condition B, use stream_wb_nopf instead\n");
            exit(1);
        }
    }

    size_t region_size = region_gb * 1024UL * 1024UL * 1024UL;
    void *buf = hugepage_alloc(region_size, node);
    if (buf == MAP_FAILED) exit(1);

    /* Touch all pages (MAP_POPULATE handles this but explicit touch is safer) */
    memset(buf, 0xAB, region_size);

    fprintf(stderr, "stream_wb: cpu=%d node=%d region=%zu GB duration=%.0f s\n",
            cpu, node, region_gb, duration_sec);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    double deadline = t0.tv_sec + t0.tv_nsec * 1e-9 + duration_sec;

    uint64_t total_bytes = 0;
    int iteration = 0;
    double last_report = t0.tv_sec + t0.tv_nsec * 1e-9;

    while (!g_stop) {
        uint64_t swept = stream_read_once((const char *)buf, region_size);
        total_bytes += swept;
        iteration++;

        clock_gettime(CLOCK_MONOTONIC, &t1);
        double now = t1.tv_sec + t1.tv_nsec * 1e-9;

        if (now - last_report >= REPORT_INTERVAL) {
            double bw_gbps = (double)(swept) / (now - last_report) / 1e9;
            fprintf(stderr, "stream_wb [cpu%d]: iter=%d bw=%.2f GB/s\n",
                    cpu, iteration, bw_gbps);
            last_report = now;
        }

        if (now >= deadline) break;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    double avg_bw = (double)total_bytes / elapsed / 1e9;

    printf("{\"cpu\": %d, \"condition\": \"A_wb_pf\", \"region_gb\": %zu, "
           "\"iterations\": %d, \"total_bytes\": %lu, "
           "\"elapsed_sec\": %.3f, \"avg_bw_gbps\": %.3f}\n",
           cpu, region_gb, iteration, total_bytes, elapsed, avg_bw);

    hugepage_free(buf, region_size);
    return 0;
}
