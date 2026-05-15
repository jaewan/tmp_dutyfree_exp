# Phase 2-BW — Bandwidth-Matched A vs B Matrix
## Date: 2026-05-10T10:24:57.815537

## Motivation

H7 failed: Phase 2 used A=2 cores (15.9 GB/s) vs B=3 cores (20.4 GB/s),
a 22% bandwidth mismatch. This re-run holds core count equal for A and B.

## Quiescent Baseline

- Q: mean=81.4, std=0.0, median=81.4 cycles/load (n=20)

## Results by (Condition, Core Count)

| Cond | Cores | n | Mean cyc/load | Std | Median | iMC BW (GB/s) | Tax vs Q |
|------|-------|---|--------------|-----|--------|--------------|----------|
| A | 2 | 20 | 255.0 | 16.0 | 250.5 | 16.0 | +213.1% |
| B | 2 | 20 | 181.4 | 55.7 | 168.2 | 14.3 | +122.8% |
| A | 3 | 20 | 280.3 | 8.4 | 276.4 | 23.7 | +244.2% |
| B | 3 | 20 | 237.3 | 33.8 | 236.5 | 21.3 | +191.4% |
| A | 4 | 20 | 294.9 | 4.9 | 293.1 | 31.3 | +262.2% |
| B | 4 | 20 | 251.3 | 28.1 | 249.1 | 27.5 | +208.6% |

## A vs B Comparison at Matched Core Counts

**H7-follow-up:** Does A > B persist when core count is held equal?

| Cores | A mean | B mean | A iMC BW | B iMC BW | Δ cyc | Cliff δ | BW ratio (A/B) |
|-------|--------|--------|---------|---------|-------|---------|----------------|
| 2 | 255.0 | 181.4 | 16.0 | 14.3 | +73.6 | +0.720 | 1.12× |
| 3 | 280.3 | 237.3 | 23.7 | 21.3 | +43.0 | +0.755 | 1.11× |
| 4 | 294.9 | 251.3 | 31.3 | 27.5 | +43.6 | +0.840 | 1.14× |

## Interpretation

- **A > B at each core count** (A−B > 0, Cliff δ > 0.5): prefetcher amplification
  confirmed independent of core count / BW confound.
- **A ≈ B or A < B at matched cores**: the original Phase 2 finding
  was driven by core count difference (bandwidth confound), not prefetcher.
- **BW ratio ≈ 1.0 at matched cores**: confirms the matching worked.

### Observed:

A > B with Cliff δ > 0.5 at core counts: [2, 3, 4]
→ Prefetcher amplification CONFIRMED at bandwidth-matched comparison.

