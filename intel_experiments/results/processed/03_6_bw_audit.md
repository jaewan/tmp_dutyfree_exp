# Phase 3.6 — Bandwidth-Matching Audit
## Date: 2026-05-10T10:13:35.642795

## Measured DRAM Bandwidth at Phase 2 Core Counts

| Cond | Cores (P2) | DRAM Read (GB/s) | DRAM Write (GB/s) | Total (GB/s) | n trials |
|------|------------|------------------|-------------------|-------------|----------|
| A | 2 | 15.8 | 0.1 | 15.9 | 5 |
| B | 3 | 20.4 | 0.1 | 20.4 | 5 |
| C | 3 | 18.8 | 0.1 | 18.8 | 5 |
| D | 2 | 12.4 | 0.1 | 12.5 | 5 |

## Per-Core Bandwidth Calibration (1 core each)

| Cond | DRAM Total (GB/s) @ 1 core |
|------|----------------------------|
| A | 7.9 |
| B | 11.6 |
| C | 6.2 |
| D | 6.2 |

## H7 Evaluation (Bandwidth Matching)

**Pre-registered:** |BW_A − BW_B| / max(BW_A,BW_B) ≤ 0.10

| Pair | BW_X (GB/s) | BW_Y (GB/s) | |ΔBW|/max | H7 |
|------|-------------|-------------|-----------|----|
| A vs B | 15.9 | 20.4 | 0.22 | FAIL |
| A vs C | 15.9 | 18.8 | 0.16 | FAIL |
| A vs D | 15.9 | 12.5 | 0.21 | FAIL |
| B vs C | 20.4 | 18.8 | 0.08 | PASS |

**H7 FAILS for A vs B.** Phase 2 comparison is bandwidth-confounded.
  BW_A = 15.9 GB/s (2 cores), BW_B = 20.4 GB/s (3 cores).
  A bandwidth-matched re-run of Phase 2 is required.
  Suggested: run both A and B at 2 cores, repeat at 3 cores.

## Method Note

DRAM bandwidth measured via iMC uncore PMU events:
  `uncore_imc_N/unc_m_cas_count.rd/` and `unc_m_cas_count.wr/`
  summed across iMC channels 0–3 (socket 0).
  Each CAS operation = 64 bytes.
  If iMC events are unavailable, `fallback=True` and values are 0.
