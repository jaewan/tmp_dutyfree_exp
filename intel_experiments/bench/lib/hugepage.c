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

void *hugepage_alloc(size_t size, int numa_node)
{
    void *p = mmap(NULL, size,
                   PROT_READ | PROT_WRITE,
                   MAP_ANONYMOUS | MAP_PRIVATE | MAP_HUGETLB | MAP_HUGE_2MB | MAP_POPULATE,
                   -1, 0);
    if (p == MAP_FAILED) {
        perror("hugepage_alloc: mmap MAP_HUGETLB");
        fprintf(stderr, "  Hint: check 2MB hugepage availability with "
                "'cat /proc/meminfo | grep HugePages_Free'\n");
        return MAP_FAILED;
    }

    if (numa_node >= 0) {
        unsigned long nodemask = 1UL << numa_node;
        if (mbind(p, size, MPOL_BIND, &nodemask, sizeof(nodemask) * 8,
                  MPOL_MF_MOVE | MPOL_MF_STRICT) < 0) {
            perror("hugepage_alloc: mbind");
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

void hugepage_free(void *p, size_t size)
{
    munmap(p, size);
}
