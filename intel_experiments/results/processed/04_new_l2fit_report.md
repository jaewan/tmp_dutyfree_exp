# Phase 4-NEW Report — L2-Fit Victim Control
## Date: 2026-05-10T10:09:30.894734

## Experimental Parameters

- Victim WSS: 384 KB (L2-fit)
- Aggressor core counts: A=2, B=3
- n = 30 trials per condition
- run_sec = 1.0

## L2 Residency Verification

- L2 residency check not run or returned no data

## Results Summary

| Cond | n | Mean cycles/load | Std | Median |
|------|---|-----------------|-----|--------|
| Q | 30 | 18.2 | 0.0 | 18.2 |
| A | 30 | 18.2 | 0.0 | 18.2 |
| B | 30 | 18.2 | 0.0 | 18.2 |

## H8 Evaluation (L2-fit reduces Q→A tax by ≥50%)

- Phase 2 (32 MB WSS): Q=81.4, A=241.8, tax=197.1%
- Phase 4-NEW (384 KB WSS): Q=18.2, A=18.2, tax=-0.0%
- Tax reduction: 100.0% (H8 threshold: ≥50% → indicates LLC capacity dominates)
- **H8: PASSES** — LLC capacity displacement explains ≥50% of Phase 2 tax.
  IMPLICATION: Phase 2 headline numbers are partially confounded by LLC capacity.
  The SF-mediation claim requires qualification.

## H9 Evaluation (A > B under L2-fit, large effect)

- A_384KB=18.2, B_384KB=18.2, A−B=-0.0
- Cliff's δ = -0.004 (threshold > 0.5)
- Welch t = -0.16, p = 0.8702 (threshold < 0.01)
- **H9: FAILS** — A ≤ B under L2-fit; prefetcher effect absent.

## Universality Verdict (UV1–UV4 components)

- UV1: NOT SUPPORTED — Tax absent under L2-fit
- UV2: NOT SUPPORTED — Prefetcher effect absent at L2 level
- UV3: Requires Phase 5-NEW (SNC isolation) — N/A if SNC disabled
- UV4: Requires Phase 6-NEW (True WC mapping)
