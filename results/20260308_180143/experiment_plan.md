# Experiment Plan (2026-03-08 18:01:43)

## Objective
Strengthen two weakly supported workshop claims with fresh, traceable evidence:
1. A decisive **intra-application** (single-process, shared-address-space) WB vs WC interference experiment.
2. A **real-workload-grounded** experiment using a lightweight columnar scan proxy as aggressor.

## Pre-run checks (required)
1. Sanity-check stale experiment processes.
2. Confirm topology/core lists and NUMA mapping.
3. Build binaries including new `bin/intra_app_corun`.
4. Reuse prior pinned placement unless invalidated by current topology evidence.

## Core mapping / controls
- Victim: core `128`, node `1` (as in recent suites).
- Aggressor candidate cores: `136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231`.
- Experiment A matched-BW pair target (from prior artifacts):
  - WB: `2` threads on first two cores.
  - WC: `9` threads on first nine cores.
- Experiment B columnar scan aggressor:
  - WB columnar scan on `8` threads (first 8 cores).

## Experiment A design
- Binary: `bin/intra_app_corun` (single process, pthreads).
- Victim: pointer chase (`cycles/iter` primary).
- Scenarios per run:
  - baseline (`mode=none`)
  - co-run WB (`mode=wb_load`, 2T)
  - co-run WC (`mode=wc_ntdqa`, 9T)
- Runs: `n=10`.
- Randomization: deterministic WB/WC order per run using fixed seed.
- Analysis:
  - delta vs baseline per run for WB and WC
  - paired WB-WC delta stats
  - Welch WB vs WC CPI and paired test on deltas (with caveat if SciPy unavailable)

## Experiment B design
- Victim unchanged (pointer chase).
- Aggressor replaced with columnar scan proxy (`mode=wb_column_scan`) over CXL-backed contiguous columns.
- Scenarios per run:
  - baseline
  - victim + columnar-scan WB aggressor
- Runs: `n=5`.
- Randomization: deterministic order of {baseline, columnar_wb} per run.
- Analysis:
  - victim degradation stats (mean/sd/n, CI)
  - scan throughput stats
  - representativeness caveat recorded in claim-impact notes.

## Execution risks
- `/dev/cxl_wc` or PAT setup unavailable.
- `perf_event_open` restrictions for victim counters.
- Runtime variance under external load.

## Data integrity rules
- No fabricated values.
- Full stdout/stderr captured per run log.
- Failed runs are retained and labeled (not dropped).
- Ledgers updated with PHASE/CMD/STDOUT/FINDING/GATE_STATUS entries.
