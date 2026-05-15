#pragma once
#include <stdint.h>

/* Returns 0 on success, -1 on failure (errno set).
 * Requires /dev/cpu/<cpu>/msr to be accessible by the calling user.
 * Run env/setup.sh as root once to grant access. */
int msr_read(int cpu, uint32_t reg, uint64_t *val);
int msr_write(int cpu, uint32_t reg, uint64_t val);

/* Disable/restore all four hardware prefetchers on a specific core.
 * MSR 0x1A4 bits [3:0]: L1-DCU-stream, DCU-IP, L2-adj, L2-stream.
 * disable_mask = 0xF disables all four.
 * Returns prior MSR value (to be passed to msr_pf_restore). */
uint64_t msr_pf_disable(int cpu, uint8_t disable_mask);
void     msr_pf_restore(int cpu, uint64_t saved_val);
