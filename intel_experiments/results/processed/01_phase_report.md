# Phase 1 Report — Bandwidth Calibration
## Date: 2026-05-10T09:01:23.059726
## Status: FAIL — see NEGATIVE_RESULTS.md

## Results

| Cond | Name | n | Mean (GB/s) | Std | Min | Max | Floor | Ceil | Pass? |
|------|------|---|-------------|-----|-----|-----|-------|------|-------|
| A | WB+prefetch | 10 | 16.30 | 0.14 | 16.12 | 16.48 | 15.0 | 35.0 | ✓ |
| B | WB-nopf | 10 | 14.34 | 0.26 | 14.08 | 14.79 | 3.0 | 10.0 | ✗ |
| C | WC (MOVNTDQA) | 10 | 12.68 | 0.13 | 12.51 | 12.89 | 2.0 | 10.0 | ✗ |

A/B bandwidth ratio: 1.14× (required ≥ 2.0×)

## Phase 2 Core Counts (target 40 GB/s aggregate)

| Cond | n_cores | Predicted agg BW (GB/s) |
|------|---------|-------------------------|
| A | 2 | 32.6 |
| B | 3 | 43.0 |
| C | 3 | 38.0 |

## Gate Conditions for Phase 2
FAIL: One or more sanity checks failed.
See NEGATIVE_RESULTS.md for diagnosis.
