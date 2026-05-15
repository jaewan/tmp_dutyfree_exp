# Replicating Paper Results

This document maps paper claims to concrete in-repo evidence and exact rerun commands.

## Primary Artifact Sets

1. Main table artifacts:
- `results/hotos_20260306/phase0_scaling/`
- `results/hotos_20260306/phase1_table2/`
- `results/hotos_20260306/phase2_table3_isobw_final/`

2. Headline statistical validation (`n=10`, mid-band chase):
- `results/20260308_090953/midband_n10/`

3. Additional evidence (single-process intra-app + columnar proxy):
- `results/20260308_180143/`

4. Latest mechanistic claim suite (CAT/RDT/PMU/PREFETCH):
- `results/hotos_20260308_145043/paper_claims_suite/`

## Reproduce Current Numbers

```bash
cd /home/domin/CoherenceTest/APNET
make -j4

# Headline n=10 paired WB vs WC
bash results/20260308_090953/scripts/run_midband_chase_n10.sh

# Intra-app decisive scenario + columnar proxy grounding
bash results/20260308_180143/scripts/run_additional_experiments.sh

# Mechanistic suite (requires sudo/resctrl)
sudo bash scripts/run_paper_claims_suite.sh
```

## Sec1/Sec2 Headline Claim Inputs

1. Mid-band WB vs WC victim impact (`n=10`, chase):
- Source: `results/20260308_090953/midband_n10/midband_summary.csv`
- Values:
  - baseline CPI: `3,635,748.444 +- 50,746.677`
  - WB CPI: `4,652,322.494 +- 2,500.031`
  - WC CPI: `3,646,398.279 +- 60,805.133`
  - WB delta vs baseline: `+27.982% +- 1.729%`
  - WC delta vs baseline: `+0.309% +- 2.125%`
  - Welch p-value (WB vs WC CPI): `1.602591e-12`

2. Single-thread throughput (latest suite, `n=5`):
- Source: `results/hotos_20260308_145043/paper_claims_suite/claims_summary.md`
- Values:
  - `wb_load`: `15.7646 +- 0.0050 GB/s`
  - `wb_prefetchnta`: `15.6770 +- 0.0034 GB/s`
  - `wc_ntdqa`: `4.1738 +- 0.0013 GB/s`

2a. Intra-application single-process WB vs WC (`n=10`, matched BW):
- Source: `results/20260308_180143/experimentA_summary.csv`
- Values:
  - baseline CPI: `3,630,646.758 +- 54,843.081`
  - WB CPI: `4,662,807.364 +- 5,251.157`
  - WC CPI: `3,647,716.038 +- 66,256.565`
  - WB delta vs baseline: `+28.454% +- 1.854%`
  - WC delta vs baseline: `+0.490% +- 2.346%`
  - paired WB-WC delta: `+27.964% +- 1.880%`
  - Welch p-value (WB vs WC CPI): `2.693411e-12`

2b. Columnar scan proxy grounding (`n=5`):
- Source: `results/20260308_180143/experimentB_summary.csv`
- Values:
  - baseline CPI: `3,629,796.452 +- 48,567.671`
  - columnar WB CPI: `4,724,913.272 +- 6,910.305`
  - columnar WB delta vs baseline: `+30.189% +- 1.763%`
  - columnar scan throughput: `49.443 +- 0.105 GB/s`
  - paired p-value (columnar WB vs baseline CPI): `9.677067e-07`

3. Matched high-band PMU proxy (`n=5`, chase):
- Source: `results/hotos_20260308_145043/paper_claims_suite/claims_summary.md`
- Values:
  - BW: WB `24.8020 +- 0.0366 GB/s`, WC `24.7932 +- 0.0179 GB/s`
  - CPI: WB `4,653,865.552 +- 6,158.104`, WC `3,658,662.645 +- 56,550.848`
  - `amd_l3/event=0x04,umask=0xff/`: WB `6.2759e9`, WC `1.1155e9` (`~5.63x`)
  - `amd_df/event=0x07,umask=0x38/`: WB `1.0637e5`, WC `1.8062e5` (opposite-direction caveat)

4. CAT and RDT proxy (latest suite, `n=5`):
- Source: `results/hotos_20260308_145043/paper_claims_suite/claims_summary.md`
- CAT:
  - no-CAT degradation: `-0.026% +- 0.093%`
  - with-CAT degradation: `+0.022% +- 0.021%`
- RDT one-way proxy:
  - baseline BW: `24.8418 +- 0.0417 GB/s`
  - one-way BW: `24.7800 +- 0.0466 GB/s`
  - drop: `0.248% +- 0.278%`

## Table Artifacts Used in Narrative

1. Table 2:
- `results/hotos_20260306/phase1_table2/table2_ready.csv`

2. Table 3:
- `results/hotos_20260306/phase2_table3_isobw_final/table3_isobw_ready.csv`

## Additional Supporting Artifact

- CXL WB pointer-chase latency:
  - `results/hotos_20260305/phase0/step02_latency.log`
  - WB latency around `281.3-282.1 ns/hop`.
