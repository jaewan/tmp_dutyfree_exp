# Phase 2 Report — CAT Sweep
## Date: 2026-05-28T15:20:39.988073
## L3 ways=15, victim_ways=12, n_aggr=8, quiescent=79.5 cyc/load

| aggr_ways | agg BW (GB/s) | tax % median | tax % mean±sd | n |
|----------:|--------------:|-------------:|--------------:|--:|
| 1 | 23.50 | -1.73 | -1.73 ± 0.01 | 30 |
| 2 | 23.52 | -1.72 | -1.72 ± 0.03 | 30 |
| 3 | 23.52 | -1.73 | -1.68 ± 0.22 | 30 |
| off | 23.51 | 14.13 | 14.57 ± 1.24 | 30 |

**Expected:** victim tax persists as aggressor ways -> 1 (CAT cannot isolate streaming fills or the snoop filter).
