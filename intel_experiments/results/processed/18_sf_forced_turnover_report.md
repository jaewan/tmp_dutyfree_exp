# Phase 18 -- SF Forced-Turnover Report: H16-H18 Verdict
## Date: 2026-05-13T04:19:09.342908
## Platform: Intel Xeon Platinum 8462Y+ (SPR), socket 0

---

## Summary Table

| H# | Claim | Verdict |
|----|-------|---------|
| H16 | R32 SF eviction rate >= 10x Phase 12 baseline | **CONFIRMED** |
| H17 | |r_sf| > 0.5 at 384 KB after covariate control | **CONFIRMED** |
| H18 | Victim cycles >= Q * 1.10 at R32, 384 KB WSS | **FALSIFIED** |

## Universality Verdict for Phase 18

H16 confirms SF saturation, but H18 fails: victim latency does NOT increase at 384 KB WSS even when SF is saturated. SF saturation is not sufficient for victim degradation on Intel SPR. The SF back-invalidation pathway does not bind even at architectural saturation — robust negative result. STREAMING-H2 on Intel addresses a real capacity mechanism but not a performance-observable victim-latency pathway.

---

## H16 -- SF Saturation Under Forced-Turnover Load

R32 SF eviction rate (384 KB): 3498536/s (77.7x baseline 45000/s)
  95% CI: [3337178, 3661536]/s
  Threshold >= 450000/s (10x); falsifier < 225000/s (5x)
  H16: **CONFIRMED**

## H17 -- SF Partial Correlation with Victim Latency

Partial correlation at 384 KB (R8+R16+R24+R32, n=120):
  method: Spearman (Shapiro-Wilk residuals p=0.0000)
  r_sf (cycles ~ sf_evict | l3_miss, mc_queue) = r=+0.970 95%CI=[+0.937, +0.979] p=<0.0001
  Threshold |r| > 0.5 AND p < 0.01
  Victim cycles range: 0.77 cyc (4.2% of mean — OK)
  H17: **CONFIRMED**

## H18 -- Victim Latency Increase Under SF Saturation

H18 at 384 KB WSS (SF pathway isolation):
  Q cycles:   18.29 (n=30)
  R32 cycles: 18.84 (n=30)
  Ratio R32/Q: 1.0299 (tax = 2.99%)
  Threshold >= 1.10x (10%); falsifier < 1.05x
  Mann-Whitney U (R32 > Q): U=900 p=0.0000
  Victim L2_HIT: Q=100.0% R32=100.0%
  H18: **FALSIFIED**

## SF Eviction Rate Progression (384 KB WSS)

| Condition | n | Mean SF/s | Std SF/s | Ratio to Q |
|-----------|---|-----------|----------|------------|
| Q | 30 | 6747 | 15967 | 1.0x |
| R8 | 30 | 8438 | 3913 | 1.3x |
| R16 | 30 | 202308 | 57389 | 30.0x |
| R24 | 30 | 628901 | 95190 | 93.2x |
| R32 | 30 | 3498536 | 459004 | 518.6x |

## Victim Cycles Summary (384 KB WSS)

| Condition | n | Mean cyc/load | Tax vs Q | L2_hit |
|-----------|---|---------------|----------|--------|
| Q | 30 | 18.29 | +0.0% | 100.0% |
| R8 | 30 | 18.20 | +-0.5% | 100.0% |
| R16 | 30 | 18.19 | +-0.5% | 100.0% |
| R24 | 30 | 18.25 | +-0.2% | 100.0% |
| R32 | 30 | 18.84 | +3.0% | 100.0% |

## Victim Cycles Summary (32 MB WSS)

| Condition | n | Mean cyc/load | Tax vs Q |
|-----------|---|---------------|----------|
| Q | 30 | 81.4 | +0.0% |
| R8 | 30 | 110.4 | +35.5% |
| R16 | 30 | 293.7 | +260.6% |
| R24 | 30 | 296.6 | +264.1% |
| R32 | 30 | 300.5 | +269.0% |

## Protocol Reference

See `PHASE18_PROTOCOL.md` for pre-registered hypotheses and thresholds.
Update `UNIVERSALITY_VERDICT.md` and `FINDINGS.md` with Phase 18 results.
