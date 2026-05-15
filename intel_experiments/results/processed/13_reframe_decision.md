# Phase 13 — Partial Correlation Analysis and Reframe Decision
## Date: 2026-05-10T18:41:55.288650

- n (active trials, excluding Q): 180
- Normality test (Shapiro-Wilk on residuals): W=0.8870, p=0.0000
- Correlation method: rank-based (Spearman)

## DR1 Partial Correlation Results

| Partial corr | r | 95% CI | t | df | p | |r|>0.3 | p<0.05 |
|-------------|---|--------|---|----|----|------|-------|
| r_sf (cycles ~ sf | llc) | +0.2064 | [0.0616, 0.3427] | +2.81 | 177 | 0.0056 | NO | YES |
| r_llc (cycles ~ llc | sf) | +nan | [nan, nan] | +nan | 177 | nan | NO | NO |
| r_mlp (cycles ~ mlp | sf, llc) | +0.7865 | [0.7231, 0.8368] | +16.90 | 176 | 0.0000 | YES | YES |

## DR2 Decision

**Reframe selected: C**

**Reframe text (verbatim from DR2):** The matched-BW A>B effect is driven by memory-controller queueing, not LLC or directory pressure.

### DR2 Branch Evaluation

- |r_mlp| > max(|r_sf|, |r_llc|)? YES → Reframe C
- |r_sf| > 0.3 AND p_sf < 0.05? NO
- |r_sf| ≤ 0.3 AND |r_llc| > 0.3 AND p_llc < 0.05? NO
- None of the above → Reframe D (escalate)

### Observations Beyond the Reframe

Summary statistics by condition:

| Cond | n | Cycles | SF evict/s | LLC vic/s | L3 miss/s | MC queue |
|------|---|--------|-----------|----------|----------|----------|
| A | 90 | 272.5 | 55413 | 0 | 7608688 | 3910648358 |
| B | 90 | 200.8 | 45026 | 0 | 7139716 | 3811343114 |

**WARNING:** LLC victim events were 0 for many trials. r_llc may be unreliable.
