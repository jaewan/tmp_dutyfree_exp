# Phase 1(a)+(b) Report — Microarchitectural Baselines
## Date: 2026-05-28T15:12:08.509808
## Aggressor cores: 8, victim WSS: 32 MB

| condition | label | agg BW (GB/s) | tax % median | tax % mean±sd | n |
|-----------|-------|--------------:|-------------:|--------------:|--:|
| A | wb_pf_on | 23.51 | 12.68 | 12.80 ± 0.64 | 30 |
| B | wb_pf_off | 23.51 | 10.17 | 11.25 ± 3.02 | 30 |
| C | wc_movntdqa | 23.52 | 12.04 | 12.12 ± 0.41 | 30 |
| E | wc_nopf | 22.20 | 8.84 | 8.89 ± 0.33 | 30 |

**Expected:** A BW >> B BW (>=2x, prefetch essential); C/E BW << A BW (software streaming caps bandwidth).
