#pragma once
#include <stdint.h>

/* Serializing timestamp: CPUID + RDTSC on entry, RDTSCP + LFENCE on exit.
 * Returns TSC ticks. Caller must account for measurement overhead
 * (calibrate with a zero-work loop; overhead typically ~20–40 cycles). */

static inline uint64_t rdtscp_start(void)
{
    uint32_t aux;
    uint64_t lo, hi;
    __asm__ volatile (
        "cpuid\n\t"
        "rdtsc\n\t"
        : "=a"(lo), "=d"(hi)
        : : "rbx", "rcx"
    );
    (void)aux;
    return (hi << 32) | lo;
}

static inline uint64_t rdtscp_end(void)
{
    uint32_t aux;
    uint64_t lo, hi;
    __asm__ volatile (
        "rdtscp\n\t"
        "lfence\n\t"
        : "=a"(lo), "=d"(hi), "=c"(aux)
    );
    return (hi << 32) | lo;
}

/* RDPMC-based cycle counter for lower overhead.
 * counter_idx: 0–3 for core PMC 0–3.
 * Requires RDPMC enabled: echo 2 > /sys/bus/event_source/devices/cpu/rdpmc */
static inline uint64_t rdpmc_read(uint32_t counter_idx)
{
    uint32_t lo, hi;
    __asm__ volatile ("rdpmc" : "=a"(lo), "=d"(hi) : "c"(counter_idx));
    return ((uint64_t)hi << 32) | lo;
}
