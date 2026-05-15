#pragma once
#include <stddef.h>

/* Allocate a region of given size using 2MB hugepages on the specified
 * NUMA node. Uses MAP_HUGETLB | MAP_HUGE_2MB | MAP_POPULATE.
 * Returns MAP_FAILED on error. Caller must munmap when done. */
void *hugepage_alloc(size_t size, int numa_node);
void  hugepage_free(void *p, size_t size);
