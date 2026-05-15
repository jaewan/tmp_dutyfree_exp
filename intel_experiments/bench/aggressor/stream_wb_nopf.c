/*
 * stream_wb_nopf.c — WB streaming aggressor, hardware prefetcher DISABLED (Condition B)
 *
 * Identical to stream_wb.c except it writes MSR 0x1A4 = 0xF before running
 * to disable all four hardware prefetchers (L1-DCU-stream, DCU-IP, L2-adj, L2-stream).
 * Restores MSR on exit.
 *
 * This is the key control condition: same aggregate WB bandwidth as condition A
 * but ~4–8× lower SF entry injection rate (demand-only fills, no prefetch look-ahead).
 *
 * Usage:
 *   ./stream_wb_nopf --cpu 1 --node 0 --region-gb 1 --duration-sec 60
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
#define REPORT_INTERVAL   1.0

static volatile sig_atomic_t g_stop = 0;
static void handle_sigterm(int sig) { (void)sig; g_stop = 1; }

static uint64_t g_saved_msr = (uint64_t)-1;
static int g_cpu = -1;

static void restore_on_exit(void)
{
    if (g_saved_msr != (uint64_t)-1 && g_cpu >= 0) {
        msr_pf_restore(g_cpu, g_saved_msr);
        fprintf(stderr, "stream_wb_nopf: MSR 0x1A4 restored on cpu%d (0x%lx)\n",
                g_cpu, g_saved_msr);
    }
}

static void pin_to_cpu(int cpu)
{
    cpu_set_t cs;
    CPU_ZERO(&cs); CPU_SET(cpu, &cs);
    if (sched_setaffinity(0, sizeof(cs), &cs) < 0) { perror("sched_setaffinity"); exit(1); }
}

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

    static struct option opts[] = {
        {"cpu",         required_argument, 0, 'c'},
        {"node",        required_argument, 0, 'n'},
        {"region-gb",   required_argument, 0, 'r'},
        {"duration-sec",required_argument, 0, 'd'},
        {0, 0, 0, 0}
    };
    int opt, idx;
    while ((opt = getopt_long(argc, argv, "c:n:r:d:", opts, &idx)) != -1) {
        switch (opt) {
            case 'c': cpu        = atoi(optarg); break;
            case 'n': node       = atoi(optarg); break;
            case 'r': region_gb  = (size_t)atol(optarg); break;
            case 'd': duration_sec = atof(optarg); break;
            default: exit(1);
        }
    }

    signal(SIGTERM, handle_sigterm);
    signal(SIGINT,  handle_sigterm);

    g_cpu = cpu;
    atexit(restore_on_exit);
    pin_to_cpu(cpu);

    /* Disable hardware prefetchers on this core */
    g_saved_msr = msr_pf_disable(cpu, 0xF);
    if (g_saved_msr == (uint64_t)-1) {
        fprintf(stderr, "stream_wb_nopf: FATAL: cannot write MSR 0x1A4 on cpu%d\n"
                "  Run: sudo env/setup.sh\n", cpu);
        exit(1);
    }

    /* Verify disable took effect (anti-pattern X8) */
    uint64_t verify = 0;
    if (msr_read(cpu, 0x1A4, &verify) < 0 || (verify & 0xF) != 0xF) {
        fprintf(stderr, "stream_wb_nopf: ERROR: MSR verify failed: got 0x%lx "
                "(expected bits[3:0]=0xF)\n", verify);
        exit(1);
    }
    fprintf(stderr, "stream_wb_nopf: MSR 0x1A4 cpu%d = 0x%lx (prefetchers OFF)\n",
            cpu, verify);

    size_t region_size = region_gb * 1024UL * 1024UL * 1024UL;
    void *buf = hugepage_alloc(region_size, node);
    if (buf == MAP_FAILED) exit(1);
    memset(buf, 0xAB, region_size);

    fprintf(stderr, "stream_wb_nopf: cpu=%d node=%d region=%zu GB duration=%.0f s\n",
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
            fprintf(stderr, "stream_wb_nopf [cpu%d]: iter=%d bw=%.2f GB/s\n",
                    cpu, iteration, bw_gbps);
            last_report = now;
        }

        if (now >= deadline) break;
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double elapsed = (t1.tv_sec - t0.tv_sec) + (t1.tv_nsec - t0.tv_nsec) * 1e-9;
    double avg_bw = (double)total_bytes / elapsed / 1e9;

    printf("{\"cpu\": %d, \"condition\": \"B_wb_nopf\", \"region_gb\": %zu, "
           "\"iterations\": %d, \"total_bytes\": %lu, "
           "\"elapsed_sec\": %.3f, \"avg_bw_gbps\": %.3f, "
           "\"msr_1a4_during\": \"0xF\"}\n",
           cpu, region_gb, iteration, total_bytes, elapsed, avg_bw);

    hugepage_free(buf, region_size);
    return 0;
}
