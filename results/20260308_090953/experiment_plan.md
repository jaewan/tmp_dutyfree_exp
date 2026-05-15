# Experiment Plan and Execution Record (2026-03-08)

## Pre-existing plan (for comparison)
Historical guidance:
- Phase A: complete low/mid/high matched Table2 with both victim profiles.
- Phase B: coherence boundary Table3 across A/B/C/D placements.
- Phase C: PMU attribution WB vs WC at matched BW.
- Optional: BW sweep figure.

## Re-evaluated plan for this audit

The draft’s highest-risk unsupported claims were not full-table coverage, but:
1. `n=10 / Welch p-value / CV` support for the headline 28% claim.
2. Mechanistic wording around MOVNTDQA and PMU attribution.
3. Distinguishing measured vs modeled/citation-only statements.

Therefore, I selected a minimal-but-sufficient execution plan:

1. Rebuild and environment sanity checks.
2. Run a fresh `n=10` randomized paired mid-band chase experiment (WB2 vs WC9) to directly validate the headline statistical claim.
3. Run a fresh unprivileged mechanistic suite for:
- WB/WC/PREFETCHNTA single-thread throughput
- Matched high-band paired WB/WC PMU proxy events (with event availability precheck)
4. Reuse previously validated table artifacts for Table2 low/high and Table3 placement sweep unless draft statements changed.
5. Patch paper text to remove unsupported specificity and mark modeled values.

## Why this differs from the historical A/B/C plan

- Did not rerun full Phase A/B grids: existing table artifacts already validated those values, while current textual risk was around unsupported significance language and mechanistic attribution.
- Added an explicit statistical rerun targeted at the exact disputed claim (`n=10`, Welch p-value, CV).
- Kept Phase C equivalent but focused on currently measurable PMU events with caveats and raw logs.
- Deferred CAT/RDT reruns due hard permission blocker; retained prior evidence and marked blocked.

## Executed commands

1. Build
```bash
cd /home/domin/CoherenceTest/APNET
make -j4
```

2. Mid-band paired chase (`n=10`)
```bash
cd /home/domin/CoherenceTest/APNET
bash results/20260308_090953/scripts/run_midband_chase_n10.sh
```
Outputs:
- `results/20260308_090953/midband_n10/run_midband_chase_n10.log`
- `results/20260308_090953/midband_n10/raw/*`
- `results/20260308_090953/midband_n10/midband_runs.csv`
- `results/20260308_090953/midband_n10/midband_summary.csv`
- `results/20260308_090953/midband_n10/results_ledger.md`

3. Mechanistic + PMU suite
```bash
cd /home/domin/CoherenceTest/APNET
bash results/20260308_090953/scripts/run_mechanistic_unpriv.sh
```
Outputs:
- `results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log`
- `results/20260308_090953/mechanistic_unpriv/raw/*`
- `results/20260308_090953/mechanistic_unpriv/mechanistic_summary.csv`
- `results/20260308_090953/mechanistic_unpriv/pmu_summary.csv`
- `results/20260308_090953/mechanistic_unpriv/pmu_summary_with_caveats.csv`
- `results/20260308_090953/mechanistic_unpriv/results_ledger.md`

## Key computed stats from executed plan

1. Mid-band chase (`n=10`):
- Baseline CPI mean/sd: `3,635,748.444 ± 50,746.677`
- WB CPI mean/sd: `4,652,322.494 ± 2,500.031`
- WC CPI mean/sd: `3,646,398.279 ± 60,805.133`
- WB delta vs baseline: `+27.982% ± 1.729%`
- WC delta vs baseline: `+0.309% ± 2.125%`
- Paired WB-WC delta: `+27.673% ± 1.727%`
- Welch p-value (WB CPI vs WC CPI): `1.602591e-12`
- CVs: baseline `1.396%`, WB `0.054%`, WC `1.668%`

2. Mechanistic + PMU (`n=5`):
- Prefetch BW:
  - `wb_load`: `15.7642 ± 0.0072 GB/s`
  - `wb_prefetchnta`: `15.6816 ± 0.0054 GB/s`
  - `wc_ntdqa`: `4.1742 ± 0.0008 GB/s`
- Matched high-band PMU pair:
  - BW: WB `24.8664 ± 0.0264 GB/s`, WC `24.8194 ± 0.0160 GB/s`
  - CPI: WB `4,653,368.808 ± 8,733.883`, WC `3,591,398.492 ± 14,691.896`
  - `amd_l3/event=0x04,umask=0xff/`: WB `6.322e9`, WC `1.198e9` (`5.277x`)
  - `amd_df/event=0x07,umask=0x38/`: WB `1.377e5`, WC `1.912e5` (opposite direction caveat)

## Blocked items

1. CAT/RDT rerun under `/sys/fs/resctrl`
- `mkdir /sys/fs/resctrl/...` failed with `Permission denied`.
- `sudo -n ...` failed with `sudo: a password is required`.
- Outcome: blocked in this audit session; retained prior validated artifacts.

## Final chosen plan status
- Completed: targeted reruns for unsupported statistical and mechanistic claims.
- Completed: paper text updates limited to empirically supported values.
- Completed: reproducibility docs updated with exact commands and artifact paths.
- Blocked with evidence: CAT/RDT reruns requiring privileged resctrl write access.
