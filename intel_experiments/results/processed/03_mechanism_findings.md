# Phase 3 Findings — Mechanism Plot
## Date: 2026-05-10T10:05:47.720763

## Data Summary by Condition

| Cond | BW range (GB/s) | SF evict/s range | n points × trials |
|------|-----------------|------------------|-----------------|
| A | 1.0–15.5 | 53535–248050 | 8×10 |
| B | 1.0–15.9 | 18354–198543 | 8×10 |
| C | 1.0–15.3 | 19536–248595 | 8×10 |

## H5 Evaluation

Linear regression (slope, R²) requires scipy — run analysis/stats.py.
Expected: A and B have positive slope (R² > 0.85); C slope ≈ 0.

## Notes

- PMU access requires perf_event_paranoid ≤ 0.
- SF eviction rate is `unc_cha_core_snp.evict_one + evict_gtone` summed across all 32 CHA tiles.
- See NEGATIVE_RESULTS.md §N1 for event name mapping.
