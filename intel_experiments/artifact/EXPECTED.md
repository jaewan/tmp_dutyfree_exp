# EXPECTED.md — Reviewer Ground Truth
## directory-tax-spr | Updated after Phase 5

**NOTE:** Numeric bounds in this file will be filled in after Phase 5 completes.
Pre-registration requires us to document expected ranges BEFORE data is collected;
these are the METHODOLOGY.md bounds. Actual measurements replace the [TBD] markers.

---

## Platform Requirements

| Requirement | Value |
|-------------|-------|
| CPU | Intel Xeon Platinum 8462Y+ (or other Sapphire Rapids SKU) |
| Minimum cores on target socket | 4 (victim + 3 aggressors) |
| Linux kernel | ≥ 5.15 with SPR uncore PMU support |
| perf_event_paranoid | ≤ 0 (set by setup.sh) |
| Turbo | OFF (set by setup.sh) |
| CPU frequency | Locked (set by setup.sh) |
| 2MB hugepages | ≥ 8192 (26 GB available on this machine) |

---

## Figure 1 — results/figures/02_matrix.pdf

**What the reviewer should see:**

Five bars (Q, A, B, C, D) representing victim pointer-chase latency (cycles/load).

Expected qualitative pattern:
1. Q (quiescent): lowest bar — the baseline LLC-hit latency (~100–160 cycles/load for 32 MB WSS).
2. A > Q by ≥ 15% (pre-registered H1 threshold). Expected range: 15–40%.
3. B > Q but < 0.5×(A-Q) above Q (pre-registered H2).
4. C ≈ Q (< 2% over Q, H3). The C bar should be nearly equal to Q.
5. D ≈ A (within 20%, H4). The D bar should be close to A.

**Ranking:** A ≥ D >> B >> C ≥ Q

**Quantitative bounds (pre-registered, from METHODOLOGY.md §H1–H4):**
| Condition | Expected (cycles/load) | Source |
|-----------|------------------------|--------|
| Q | 100–160 | LLC-hit latency, 32 MB random walk |
| A | Q × 1.15 – Q × 1.40 | H1 (15% minimum, 40% conservative ceiling) |
| B | Q + (A-Q) × 0.1 to Q + (A-Q) × 0.5 | H2 |
| C | Q × 0.98 – Q × 1.02 | H3 |
| D | A × 0.80 – A × 1.20 | H4 |

Error bars: ± 1 standard deviation. Individual trial points overlaid.

**If these bounds are not met:** See NEGATIVE_RESULTS.md for diagnosis.
The key falsifier is C > Q × 1.05 — if observed, halt and write BLOCKING.md.

---

## Figure 2 — results/figures/03_mechanism.pdf

**What the reviewer should see:**

A scatter plot with regression lines: SF eviction rate (y-axis, evict/s) vs.
aggregate streaming bandwidth (x-axis, GB/s) for conditions A, B, C, D.

Expected qualitative pattern:
- Conditions A and B: **positive linear slope** with R² > 0.85.
- Condition C: **near-flat**, slope statistically indistinguishable from 0.
- Condition A slope > condition B slope per unit bandwidth (H5).
- Condition D: similar to A (advisory NT hint on WB pages).

**This is the key mechanism decomposition figure for the paper's §2 claim.**

If C shows a positive slope: the SF enrollment mechanism is present even for
NT traffic — a surprising finding that would require re-examining the claim.

---

## Figure 3 — results/figures/04_wss.pdf

**What the reviewer should see:**

Tax (%) vs. victim WSS (MB, log₂ scale). Expected shape:
- Low tax at WSS ≤ 1 MB (fits in L2 — SF pressure cannot easily evict lines).
- Tax grows in 4–32 MB range (LLC resident, vulnerable to SF eviction).
- Tax may plateau or decrease at WSS > 60 MB (victim DRAM-bound regardless).

---

## Figure 4 — results/figures/04_aggressors.pdf

**What the reviewer should see:**

Tax (%) vs. number of aggressor cores (1–16), dual y-axis with aggregate BW.
Expected: monotonically increasing tax with aggressor count as bandwidth grows.
Saturation may occur when SF pressure exceeds a threshold.

---

## Table 1 — results/processed/05_stats_table.md

For each pairwise comparison (10 total):
- Mean difference, 95% CI, Welch t, df, p (raw), p (Bonferroni-corrected).
- Cliff's delta effect size with magnitude classification.
- Shapiro-Wilk normality for each sample.

**Key comparisons (must be significant after Bonferroni, p < 0.005):**
- A vs. Q: must show p < 0.005, Cliff's δ ≥ medium (H1).
- A vs. C: must show p < 0.005, C < A (H3 + H6 — the primary universality claim).
- A vs. B: should show p < 0.005 with B < A (H2 — prefetcher effect).

---

## Known Sources of Variance

1. **TSC measurement noise**: The rdtscp-fenced pointer chase has ~20–40 cycle
   overhead per measurement call. For 1-second runs with millions of loads,
   this contribution is negligible.

2. **Frequency wobble**: Even with turbo off and governor=performance, SPR cores
   may have ±1–2% frequency variation across trials due to thermal feedback.
   This introduces ~2–4 cycle variation in the per-load latency estimate.
   Impact: small relative to the expected 15–40% effect.

3. **NUMA imbalance**: All experiments pin to socket 0, node 0. Cross-socket
   interference is negligible.

4. **Aggressor BW variation**: Per-iteration bandwidth varies by ±5–10% due to
   memory controller scheduling. The matched-bandwidth condition is approximate;
   actual aggregate BW is recorded per trial.

5. **OS scheduler**: With no isolcpus, kernel background tasks may occasionally
   land on the victim or aggressor cores. This is logged via rdtscp outlier
   detection. Outlier trials (> 5× IQR above median) are flagged but kept in
   the n=30 sample.

6. **2MB vs. 1G hugepages**: 2MB hugepages are used instead of 1G. For 1 GB
   streaming regions, this means 512 TLB entries per aggressor. This is within
   the L2 TLB capacity and does not significantly affect streaming throughput.

---

## Reproduction Tolerance

A reproduction is considered **successful** if:

| Metric | Tolerance |
|--------|-----------|
| A/Q ratio | ≥ 1.10 (slightly relaxed from 1.15 for cross-machine variance) |
| B/A ratio | ≤ 0.75 (B shows lower tax than A) |
| C/Q ratio | ≤ 1.05 (C near quiescent) |
| SF eviction slope (cond A) | > 0 with p < 0.05 |
| All H3-threatening results | Reported in NEGATIVE_RESULTS.md |

A reproduction that does NOT meet the A/Q ≥ 1.10 threshold but shows
B > C > Q in the right direction is a **partial reproduction** — the
qualitative mechanism holds but the magnitude is smaller on this platform.
Document and report.
