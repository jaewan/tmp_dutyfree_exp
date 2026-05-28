# Phase 1(c) Report — TLB Isolation
## Date: 2026-05-28T15:13:45.308678

| page_kb | agg BW (GB/s) | victim tax % (mean±sd) | n |
|--------:|--------------:|-----------------------:|--:|
| 4 | 23.48 | 15.86 ± 1.64 | 20 |
| 2048 | 23.51 | 15.36 ± 0.64 | 20 |

**Expected:** victim tax roughly flat across page sizes → bottleneck is data-array/LLC eviction, not L2-TLB thrashing.
