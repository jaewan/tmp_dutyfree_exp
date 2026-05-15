# Agent Claim Audit (2026-03-08)

## Scope
- Draft files audited:
  - `Paper/APNet/Sec1_Introduction.tex`
  - `Paper/APNet/Sec2.tex`
  - `Paper/APNet/Sec3.tex`
  - `Paper/APNet/Sec4.tex`
  - `Paper/APNet/Sec5.tex`
- New evidence generated in this audit run:
  - `results/20260308_090953/midband_n10/*`
  - `results/20260308_090953/mechanistic_unpriv/*`

## Claim-by-claim status

### Sec1_Introduction.tex

1. Claim (`Sec1_Introduction.tex:58-60`): CXL latency spans ~200ns to >500ns across deployments.
- Status: citation-only/theoretical
- Evidence: external citations only (`cxl_practice`, `xconn`, `ufx`), not measured in this repo.

2. Claim (`Sec1_Introduction.tex:61-64`): Little's-Law in-flight-line requirements (e.g., 62 at 16GB/s, 250ns).
- Status: citation-only/theoretical
- Evidence: analytic calculation, not a measured counter.

3. Claim (`Sec1_Introduction.tex:67-68`): per-core throughput is ~4.2 GB/s WC vs ~15.8 GB/s WB (~3.8x).
- Status: validated by current repo artifacts
- Evidence:
  - `results/hotos_20260306/phase0_scaling/phase0_scaling_summary.csv`
  - `results/20260308_090953/mechanistic_unpriv/pmu_summary.csv` (`wb_load=15.7642`, `wc_ntdqa=4.1742`, `n=5`)

4. Claim (`Sec1_Introduction.tex:69-85`): WB prefetch gating and WB directory enrollment coupling is architectural.
- Status: partially validated
- Evidence:
  - Measured WB/WC throughput behavior on this AMD host (`results/20260308_090953/mechanistic_unpriv/pmu_summary.csv`)
  - Intel-side behavior in-text is citation-based (manuals), not locally measured.

5. Claim (`Sec1_Introduction.tex:114-117`): ~28% WB victim inflation at matched BW, WC <1%.
- Status: validated by current repo artifacts
- Evidence:
  - New run: `results/20260308_090953/midband_n10/midband_summary.csv`
  - WB delta: `+27.982%` (sd `1.729`, n=10)
  - WC delta: `+0.309%` (sd `2.125`, n=10)

### Sec2.tex

6. Claim (`Sec2.tex:4-6`): directory enrollment is dominant over link/MC/LLC-array contention.
- Status: partially validated
- Evidence:
  - Controlled WB vs WC matched-BW experiments show large differential (`results/20260308_090953/midband_n10/midband_summary.csv`).
  - Strong causal language remains mechanistic inference; no direct directory occupancy telemetry exists.

7. Claim (`Sec2.tex:47-51`): WB per-core ~15.8 GB/s, WC ~4.2 GB/s (~4x core count for match).
- Status: validated by current repo artifacts
- Evidence:
  - `results/hotos_20260306/phase0_scaling/phase0_scaling_summary.csv`
  - `results/20260308_090953/mechanistic_unpriv/pmu_summary.csv`

8. Claim (`Sec2.tex:68-78`): at ~21 GB/s, WB causes ~28% inflation; n=10; Welch p<0.001; CV<2%.
- Status: validated by current repo artifacts
- Evidence:
  - `results/20260308_090953/midband_n10/midband_summary.csv`
  - `welch_p_wb_vs_wc_cpi=1.60e-12`
  - CVs: baseline `1.396%`, WB `0.054%`, WC `1.668%`

9. Claim (`Sec2.tex:85-93`): ~328M entries/s injection and ~12ms average residency at 4M-entry capacity.
- Status: citation-only/theoretical
- Evidence: arithmetic model from BW and capacity assumptions; no direct PF occupancy counter.

10. Claim (`Sec2.tex:105-110`): no PMU exposes directory occupancy/fill/eviction directly.
- Status: partially validated
- Evidence:
  - Local PMU access confirms proxy events (`amd_df`, `amd_l3`) exist.
  - No direct Probe Filter occupancy counter surfaced in our runs.
  - Exhaustive “no PMU exists” assertion remains broader than local proof.

11. Claim (`Sec2.tex:164-166`): 28% severity figure, n=10, p<0.001.
- Status: validated by current repo artifacts
- Evidence:
  - `results/20260308_090953/midband_n10/midband_summary.csv`

12. Claim (`Sec2.tex:168-179`, pre-patch): MOVNTDQA-specific PMU equivalence assertion.
- Status: unvalidated/unsupported (pre-patch wording)
- Evidence:
  - Current rerun did not collect WB VMOVDQA-vs-MOVNTDQA PMU pair.
  - Text updated to supported claims: throughput behavior + matched-BW WB/WC PMU proxy divergence.

13. Claim (`Sec2.tex:180-184`): single-way L3 restriction retains >96% bandwidth (24.854 vs 24.820, n=5).
- Status: validated by current repo artifacts (not rerun in this audit)
- Evidence:
  - `results/hotos_20260307_085142/paper_claims_suite/claims_summary.md`
- Rerun status in this audit: blocked by resctrl write permissions (see blocker section below).

### Sec3.tex

14. Claim (`Sec3.tex:28-30`): ~70 concurrent lines needed for CXL latencies.
- Status: citation-only/theoretical
- Evidence: Little's-law derivation.

15. Claim (`Sec3.tex:174-176`): 1-2us teardown invalidation for 256KB partition.
- Status: citation-only/theoretical (modeled estimate)
- Evidence:
  - Not measured on shipping hardware in this repo.
  - Text patched to explicitly label estimate.

16. Claim (`Sec3.tex:205-207`): one-way L3 proxy drop 0.137%±0.135%, n=5.
- Status: validated by current repo artifacts (not rerun in this audit)
- Evidence:
  - `results/hotos_20260307_085142/paper_claims_suite/claims_summary.md`
- Rerun status in this audit: blocked by resctrl write permissions.

### Sec4.tex

17. Claim (`Sec4.tex:59-60`): context-switch flush cost ~1-2us for 256KB partition.
- Status: citation-only/theoretical (modeled estimate)
- Evidence: no direct measurement artifact in repo; text patched to “estimated”.

### Sec5.tex

18. Claim set (`Sec5.tex`, all paragraphs): directory telemetry, dynamic renegotiation, cross-ISA standardization.
- Status: citation-only/theoretical
- Evidence: research agenda/proposals; no direct numeric local measurements required.

## Blockers and diagnostics

1. `resctrl` write permission blocker for CAT/RDT reruns
- Command:
  - `mkdir /sys/fs/resctrl/apnet_probe_<pid>`
- Observed error:
  - `Permission denied`
- Elevated attempt:
  - `sudo -n mkdir -p /sys/fs/resctrl/apnet_probe_test`
- Observed error:
  - `sudo: a password is required`
- Impact:
  - CAT/RDT phases from `scripts/run_paper_claims_suite.sh` could not be rerun in this audit session.
- Mitigation:
  - Retained prior validated artifacts (`results/hotos_20260307_085142/paper_claims_suite/*`) and marked rerun as blocked.

2. CXL device access blocker under non-elevated runs
- Command:
  - `bash results/20260308_090953/scripts/run_midband_chase_n10.sh` (non-elevated)
- Observed error in raw log:
  - `/dev/cxl_wc: Permission denied`
- Mitigation:
  - Reran all new measurement scripts with elevated permissions.

## Net status summary
- Validated now with fresh logs: headline ~28% WB-vs-WC interference claim at matched BW with `n=10`, statistical tests, CV.
- Validated now with fresh logs: WB/WC throughput gap and PMU proxy divergence under matched high bandwidth.
- Validated from prior artifacts but not rerun due permissions: CAT/RDT one-way proxy values.
- Rewritten/retagged as modeled or citation-only: teardown/context-switch microsecond estimates and broad non-local claims.
