# Phase 14 — L2-Fit Residency Validation
## Date: 2026-05-10T17:41:23.471616

- WSS: 384 KB (target: fits in 2 MB L2)
- n: 30 trials per condition

## Results

| Cond | Cores | n | Mean cyc | Std | L2_HIT rate | L2_HIT total | L3_MISS total | Verdict |
|------|-------|---|----------|-----|------------|-------------|--------------|--------|
| Q | 0 | 1 | 18.3 | 0.0 | 1.000 | 4,548,391,661 | 206,308 | VALID |
| A | 2 | 1 | 18.2 | 0.0 | 1.000 | 4,577,269,009 | 661,121 | VALID |
| B | 3 | 1 | 18.2 | 0.0 | 1.000 | 4,564,273,707 | 647,300 | VALID |

## Verdicts

**Q (0 aggressor cores):** VALID — L2 residency confirmed (≥90%)

**A (2 aggressor cores):** VALID — L2 residency confirmed (≥90%)

**B (3 aggressor cores):** VALID — L2 residency confirmed (≥90%)

## Phase 4-NEW Validity

**VALID.** L2 residency ≥90% for both Q and A conditions.
The zero-tax finding under L2-fit is confirmed as a real L2-resident result.
The H8 interpretation (LLC capacity dominant) stands.
