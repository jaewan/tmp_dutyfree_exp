# Phase 3.5 — Mechanism Analysis
## Date: 2026-05-10T10:05:53.011184

## Data Summary

| Cond | n points | n trials | BW range (GB/s) | SF evict/s mean | SF evict/s std |
|------|----------|----------|-----------------|----------------|----------------|
| A | 8 | 80 | 1.0–15.4 | 93694 | 35659 |
| B | 8 | 80 | 1.0–15.8 | 62911 | 28303 |
| C | 8 | 80 | 1.1–15.0 | 106101 | 42442 |

## Linear Regression Results (SF evict/s ~ agg_bw)

| Cond | Slope (ev/s per GB/s) | SE | R² | p-value |
|------|-----------------------|----|----|---------|
| A | -1239 | 2399 | 0.095 | 0.6057 |
| B | -53 | 797 | 0.001 | 0.9471 |
| C | 3463 | 1688 | 0.620 | 0.0402 |

## H5 Evaluation
**Pre-registered:** A and B slope > 0, R² > 0.85; C slope ≈ 0.

- Condition A: slope=-1239 ev/s per GB/s, R²=0.095, p=0.6057, n=8 → **FAIL**
- Condition B: slope=-53 ev/s per GB/s, R²=0.001, p=0.9471, n=8 → **FAIL**
- Condition C: slope=3463 ev/s per GB/s, R²=0.620, p=0.0402, n=8 → **FAIL (slope ≠ 0)**

## H12 Setup Note

H12 requires victim cycles as dependent variable alongside sf_evict_rate.
Phase 3 did not run a concurrent victim; the correlation test requires
Phase 4-NEW data where victim cycles and SF rates are measured together.
The regression model is:
  victim_cycles ~ sf_evict_rate + llc_miss_rate + agg_bw_gbps

## Notes

- SF eviction rate is `evict_one + evict_gtone` summed across 32 CHA tiles.
- agg_bw_gbps values are as reported by stream_wb (likely underestimated ~16×).
  Regression axis should be interpreted as relative BW proxy, not absolute.
- High trial-to-trial variance in SF evict/s is expected: perf stat collects
  a single window; transient SF pressure bursts affect individual measurements.
- See Phase 3.6 for actual bandwidth measurement.
- See DIAGNOSIS.md §2 for identifiability context.
