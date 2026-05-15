# Phase 17 — SF Saturation Report: H13–H15 Verdict
## Date: 2026-05-13T02:43:20.056526
## Platform: Intel Xeon Platinum 8462Y+ (SPR), socket 0

---

## Summary Table

| H# | Claim | Verdict |
|----|-------|---------|
| H13 | R32 SF eviction rate ≥ 10× baseline at 384 KB | **FALSIFIED** |
| H14 | r_sf > 0.5 after controlling for l3_miss + mc_queue | **CONFIRMED** |
| H15 | S32 SF rate ≤ 20% of R32; tax eliminated ≥ 50% | **INSUFFICIENT DATA** |

## Universality Verdict for Phase 17

H13 falsifies: Intel SPR SF cannot be saturated under the tested random-access workload scale. Universality claim is architectural-only.

---

## H13 — SF Saturation Under 32-Core Random-Access Load

R32 SF eviction rate (384 KB): 20791/s (0.5× baseline 45000/s)
  95% CI: [12827, 31370]/s
  Threshold ≥ 450000/s (10×); falsifier < 225000/s (5×)
  H13: **FALSIFIED**

## H14 — SF Partial Correlation with Victim Latency

Partial correlation at 384 KB (R16+R24+R32, n=90):
  method: Spearman (Shapiro-Wilk residuals p=0.0003)
  r_sf (cycles ~ sf_evict | l3_miss, mc_queue) = r=+0.559 95%CI=[+0.331, +0.732] p=<0.0001
  Threshold |r| > 0.5 AND p < 0.01
  H14: **CONFIRMED**

## H15 — STREAMING-Proxy (CLDEMOTE) Mitigation

Missing conditions at 384 KB: ['S32']

## SF Eviction Rate Progression (384 KB WSS)

| Condition | n | Mean SF/s | Std SF/s | Ratio to Q |
|-----------|---|-----------|----------|------------|
| Q | 30 | 88760 | 28554 | 1.0× |
| R16 | 30 | 52098 | 45661 | 0.6× |
| R24 | 30 | 17388 | 21999 | 0.2× |
| R32 | 30 | 20791 | 26834 | 0.2× |

## Victim Cycles Summary (384 KB WSS)

| Condition | n | Mean cyc/load | Tax vs Q | L2_hit rate |
|-----------|---|---------------|----------|-------------|
| Q | 30 | 18.4 | +0.0% | 100.0% |
| R16 | 30 | 18.2 | +-1.1% | 100.0% |
| R24 | 30 | 18.2 | +-1.4% | 100.0% |
| R32 | 30 | 18.1 | +-1.5% | 100.0% |

## Data Completeness

- victim_cycles (> 0): PRESENT
- sf_evictions (> 0): PRESENT
- sf_victim_per_sec: MISSING — see PMU_SUBSTITUTIONS.md
- l3_misses (> 0): PRESENT
- mc_queue (> 0): PRESENT
- imc_bw (> 0): MISSING — see PMU_SUBSTITUTIONS.md

## Protocol Reference

See `PHASE17_PROTOCOL.md` for pre-registered hypotheses and thresholds.
Update `UNIVERSALITY_VERDICT.md` with Phase 17 results per §Outputs.
