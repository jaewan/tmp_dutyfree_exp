#include "msr.h"
#include <fcntl.h>
#include <unistd.h>
#include <stdio.h>
#include <errno.h>

#define MSR_PREFETCH_CTRL 0x1A4

static int msr_open(int cpu, int flags)
{
    char path[64];
    snprintf(path, sizeof(path), "/dev/cpu/%d/msr", cpu);
    int fd = open(path, flags);
    if (fd < 0)
        perror(path);
    return fd;
}

int msr_read(int cpu, uint32_t reg, uint64_t *val)
{
    int fd = msr_open(cpu, O_RDONLY);
    if (fd < 0) return -1;
    ssize_t n = pread(fd, val, sizeof(*val), (off_t)reg);
    close(fd);
    return (n == sizeof(*val)) ? 0 : -1;
}

int msr_write(int cpu, uint32_t reg, uint64_t val)
{
    int fd = msr_open(cpu, O_WRONLY);
    if (fd < 0) return -1;
    ssize_t n = pwrite(fd, &val, sizeof(val), (off_t)reg);
    close(fd);
    return (n == sizeof(val)) ? 0 : -1;
}

uint64_t msr_pf_disable(int cpu, uint8_t disable_mask)
{
    uint64_t old = 0;
    if (msr_read(cpu, MSR_PREFETCH_CTRL, &old) < 0) {
        fprintf(stderr, "msr_pf_disable: read MSR 0x1A4 failed on cpu%d\n", cpu);
        return (uint64_t)-1;
    }
    uint64_t nval = (old & ~0xFULL) | (disable_mask & 0xF);
    if (msr_write(cpu, MSR_PREFETCH_CTRL, nval) < 0) {
        fprintf(stderr, "msr_pf_disable: write MSR 0x1A4 failed on cpu%d\n", cpu);
        return (uint64_t)-1;
    }
    /* Verify the write took effect (anti-pattern X8) */
    uint64_t verify = 0;
    if (msr_read(cpu, MSR_PREFETCH_CTRL, &verify) < 0 || verify != nval) {
        fprintf(stderr, "msr_pf_disable: verify failed cpu%d: wrote 0x%lx read 0x%lx\n",
                cpu, nval, verify);
    }
    return old;
}

void msr_pf_restore(int cpu, uint64_t saved_val)
{
    if (saved_val == (uint64_t)-1) return;
    if (msr_write(cpu, MSR_PREFETCH_CTRL, saved_val) < 0)
        fprintf(stderr, "msr_pf_restore: write failed on cpu%d\n", cpu);
}
