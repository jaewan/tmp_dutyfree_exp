# Claim Impact Assessment (results/20260308_180143)

## Newly validated by this run

1. **Decisive intra-application scenario is real and strong.**
- Evidence: single-process pthread co-run (`bin/intra_app_corun`) with victim+aggressor in one address space.
- Source: `experimentA_summary.csv`, `all_runs.csv`, `raw/expA/*.log`.
- Measured (`n=10`):
  - WB delta vs baseline: `+28.454% ± 1.854%` (mean ± sd)
  - WC delta vs baseline: `+0.490% ± 2.346%`
  - Paired WB-WC delta: `+27.964% ± 1.880%`
  - BW match: WB `20.906 ± 0.048` GB/s, WC `20.923 ± 0.020` GB/s
  - Statistical tests:
    - Welch WB-vs-WC CPI: `p=2.693411e-12`
    - Paired test on run-level deltas: `p=4.433676e-12`

2. **Real-workload grounding with a columnar scan proxy shows comparable harm.**
- Implementation: contiguous two-column CXL scan (`wb_column_scan`) with predicate + projection.
- Source: `experimentB_summary.csv`, `all_runs.csv`, `raw/expB/*.log`.
- Measured (`n=5`):
  - Victim delta vs baseline under columnar WB scan: `+30.189% ± 1.763%`
  - Scan throughput: `49.443 ± 0.105` GB/s
  - Paired test (columnar WB vs baseline CPI): `p=9.677067e-07`

## Unchanged claims (still supported by earlier artifacts)

1. PREFETCHNTA behaves WB-like (throughput + victim impact context):
- `results/hotos_20260308_145043/paper_claims_suite/prefetch/*`

2. CAT and RDT proxy outcomes:
- `results/hotos_20260308_145043/paper_claims_suite/cat/*`
- `results/hotos_20260308_145043/paper_claims_suite/rdt_proxy/*`

3. PMU proxy evidence and caveat framing:
- `results/hotos_20260308_145043/paper_claims_suite/pmu/*`

## Still unsupported / intentionally limited

1. **Full production workload claim** (e.g., complete Arrow/Parquet engine) remains unproven.
- This run uses a **columnar scan proxy**, not a full DBMS pipeline.

2. **WC-path columnar contrast** not executed.
- This run targeted required Option 1 WB columnar grounding first; WC columnar variant remains future work.

## Test-method note

- Primary inferential claim for Experiment A uses a **paired run-level test** on `(WB-baseline)%` vs `(WC-baseline)%`, justified by within-run pairing and deterministic randomized WB/WC order.
- Welch WB-vs-WC CPI is reported as complementary unpaired robustness check.
