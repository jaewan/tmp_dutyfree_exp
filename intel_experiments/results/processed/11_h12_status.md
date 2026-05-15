# Phase 11 — H12 Evaluation Status
## Date: 2026-05-10

## Can H12 Be Computed from Existing Data?

**Answer: NO.** H12 requires partial correlation of victim cycles with SF eviction
rate, controlling for LLC miss rate and aggregate bandwidth. The required data
combination does not exist in any current dataset.

### Step 1: Phase 3 Data Inspection

Phase 3 (03_pmu_sweep.csv) columns:
  condition, n_aggr_cores, trial, agg_bw_gbps, sf_evict_total, sf_evict_rate,
  sf_victim_retry, tor_drd_pref, pmu_raw

Missing: victim_cycles_per_load, victim_llc_miss_rate, victim_l3_miss_rate.
Phase 3 was a PMU sweep on aggressor side only — NO victim was running.

Conclusion: victim_cycles column does not exist in Phase 3 data. H12 cannot be
computed from Phase 3 alone.

### Step 2: Phase 4-NEW Data Inspection

Phase 4-NEW (04_new_l2fit_matrix.csv) columns:
  condition, n_aggr_cores, trial, cycles_per_load, total_loads, elapsed_sec,
  wss_bytes, imc_bw_total_gbps, imc_fallback

Missing: sf_evict_rate, victim_llc_miss_rate, victim_l3_miss_rate.
Phase 4-NEW collected victim latency and aggregate bandwidth but no SF or LLC PMU.

Conclusion: sf_evict_rate column does not exist in Phase 4-NEW data. H12 cannot
be computed from Phase 4-NEW data.

### Step 3: Phase 2-BW Data Inspection

Phase 2-BW (02_bw_matched_matrix.csv) columns:
  condition, n_cores, trial, cycles_per_load, total_loads, elapsed_sec,
  imc_bw_total_gbps, imc_bw_read_gbps, imc_bw_write_gbps, imc_fallback,
  agg_reported_bw

Missing: sf_evict_rate, victim_llc_miss_rate.

Conclusion: H12 cannot be computed from Phase 2-BW data.

## H12 Status: DEFERRED to Phase 12

**Action:** Phase 12 (full-PMU matched-BW experiment) will collect all required
signals simultaneously per trial:
  - victim cycles per load (from pointer_chase)
  - SF eviction rate at victim's CHA tile
  - LLC victim rate at victim's CHA tile (or equivalent)
  - L3 miss rate at victim's core (MEM_LOAD_RETIRED.L3_MISS)
  - aggregate bandwidth (iMC PMU)
  - memory controller queue occupancy (UNC_M_RPQ_OCCUPANCY)

Once Phase 12 data exists, H12 will be computed as:
  victim_cycles ~ β1·sf_evict_rate + β2·llc_victim_rate + β3·agg_bw_gbps
  Test: β1 (sf_evict_rate coefficient) ≠ 0 at p < 0.01

And partial correlations per DR1:
  r_sf = partialcorr(victim_cycles, sf_evictions | llc_victims)
  r_llc = partialcorr(victim_cycles, llc_victims | sf_evictions)
  r_mlp = partialcorr(victim_cycles, mc_queue_occ | sf, llc)

## N13 — H12 Not Evaluable From Existing Data

Added to NEGATIVE_RESULTS.md as N13:

"H12 (partial correlation of victim cycles with SF eviction rate after controlling
for LLC miss rate) cannot be evaluated from any collected dataset because no
experiment simultaneously measured both victim-side latency AND aggressor-side
SF eviction rates AND victim-side LLC miss rates in the same 1-second perf window.
This is a design gap. Phase 12 closes it."
