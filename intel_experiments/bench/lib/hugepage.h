#pragma once
#include <stddef.h>

/* Allocate `size` bytes on `numa_node` using the requested backing page size.
 *   page_kb == 4        -> ordinary 4KB pages (MAP_ANONYMOUS|MAP_PRIVATE, no HUGETLB)
 *   page_kb == 2048     -> 2MB hugepages (MAP_HUGE_2MB)
 *   page_kb == 1048576  -> 1GB hugepages (MAP_HUGE_1GB)
 * Always uses MAP_POPULATE and binds to `numa_node` (MPOL_BIND) when >= 0.
 * Returns MAP_FAILED on error. Caller must munmap when done. */
void *hugepage_alloc_sz(size_t size, int numa_node, size_t page_kb);

/* Backward-compatible wrapper: 2MB hugepages (== hugepage_alloc_sz(size,node,2048)). */
void *hugepage_alloc(size_t size, int numa_node);

void  hugepage_free(void *p, size_t size);
