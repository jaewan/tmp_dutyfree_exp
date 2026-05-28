#define _GNU_SOURCE
#include "hugepage.h"
#include <sys/mman.h>
#include <numa.h>
#include <numaif.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>

#ifndef MAP_HUGE_2MB
#define MAP_HUGE_2MB (21 << MAP_HUGE_SHIFT)
#endif
#ifndef MAP_HUGE_1GB
#define MAP_HUGE_1GB (30 << MAP_HUGE_SHIFT)
#endif

void *hugepage_alloc_sz(size_t size, int numa_node, size_t page_kb)
{
    int flags = MAP_ANONYMOUS | MAP_PRIVATE | MAP_POPULATE;
    if (page_kb == 2048) {
        flags |= MAP_HUGETLB | MAP_HUGE_2MB;
    } else if (page_kb == 1048576) {
        flags |= MAP_HUGETLB | MAP_HUGE_1GB;
    } else if (page_kb != 4) {
        fprintf(stderr, "hugepage_alloc_sz: unsupported page_kb=%zu "
                "(use 4, 2048, or 1048576)\n", page_kb);
        return MAP_FAILED;
    }

    void *p = mmap(NULL, size, PROT_READ | PROT_WRITE, flags, -1, 0);
    if (p == MAP_FAILED) {
        perror("hugepage_alloc_sz: mmap");
        fprintf(stderr, "  page_kb=%zu size=%zu — check availability: "
                "'cat /proc/meminfo | grep Huge' or reserve 1GB pages\n",
                page_kb, size);
        return MAP_FAILED;
    }

    if (numa_node >= 0) {
        unsigned long nodemask = 1UL << numa_node;
        if (mbind(p, size, MPOL_BIND, &nodemask, sizeof(nodemask) * 8,
                  MPOL_MF_MOVE | MPOL_MF_STRICT) < 0) {
            perror("hugepage_alloc_sz: mbind");
            fprintf(stderr, "  Hint: check NUMA node %d exists\n", numa_node);
            munmap(p, size);
            return MAP_FAILED;
        }
    }

    /* Touch pages to ensure physical allocation (MAP_POPULATE handles this,
     * but explicit touch gives us a deterministic fault-in point). */
    memset(p, 0, size);
    return p;
}

void *hugepage_alloc(size_t size, int numa_node)
{
    return hugepage_alloc_sz(size, numa_node, 2048);
}

void hugepage_free(void *p, size_t size)
{
    munmap(p, size);
}
