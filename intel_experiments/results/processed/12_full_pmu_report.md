# Phase 12 — Matched-Bandwidth Full-PMU Report
## Date: 2026-05-10T18:29:00.967951

- WSS: 32 MB
- n_trials: 30 per cell
- Measure window: 10.0s per trial

## Quiescent Baseline

| Q cycles | L3 miss/s | SF evict/s | n |
|----------|-----------|-----------|---|
| 81.8 | 19711 | 45076 | 30 |

## Per-Cell Summary

| Cond | Cores | n | Cyc/load | Tax | L3/s | SF evict/s | LLC vic/s | L2 hit% | BW GB/s |
|------|-------|---|----------|-----|------|-----------|----------|---------|--------|
| A | 2 | 30 | 246.2 | +201% | 7537906 | 49621 | 0 | 6.8 | 0.0 |
| B | 2 | 30 | 154.3 | +89% | 6736695 | 46967 | 0 | 7.5 | 0.0 |
| A | 3 | 30 | 277.4 | +239% | 7656584 | 71933 | 0 | 6.7 | 0.0 |
| B | 3 | 30 | 211.3 | +158% | 7284789 | 41685 | 0 | 7.0 | 0.0 |
| A | 4 | 30 | 293.9 | +259% | 7631574 | 44686 | 0 | 6.7 | 0.0 |
| B | 4 | 30 | 236.8 | +189% | 7397663 | 46424 | 0 | 6.9 | 0.0 |

## H12 Data Completeness

- victim_cycles: PRESENT
- sf_evictions: PRESENT
- llc_victims: MISSING — see PMU_SUBSTITUTIONS.md
- l3_misses: PRESENT
- imc_bw: MISSING — see PMU_SUBSTITUTIONS.md

MISSING COLUMNS: H12 partial correlation will be limited.
