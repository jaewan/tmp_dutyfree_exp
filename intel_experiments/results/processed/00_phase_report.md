# Phase 0 Report — Environment Capture
## Date: 2026-05-10
## Status: COMPLETE — gate conditions documented; setup.sh must be run before Phase 1

---

## Hypotheses Evaluated
Phase 0 has no measurement hypotheses. The purpose is to record hardware
state and pre-register all hypotheses for Phases 1–5.

## Deliverables Produced

| File | Status |
|------|--------|
| METHODOLOGY.md | WRITTEN — pre-registered H1–H6 with quantitative bounds |
| env/env_report.md | WRITTEN — partial (items marked [NEEDS ROOT] pending) |
| env/setup.sh | WRITTEN — idempotent root-time configuration |
| env/validate.sh | WRITTEN — pre-flight gate checks |
| NEGATIVE_RESULTS.md | WRITTEN — N0–N3 documented |
| README.md | WRITTEN |

## Hardware Confirmed

- **CPU:** Intel Xeon Platinum 8462Y+ (Sapphire Rapids), stepping 8,
  microcode 0x2b000639. Target platform confirmed.
- **Topology:** 2-socket system. Socket 0 = NUMA node 0 (CPUs 0–31, 64–95).
  All experiments use socket 0 only. SNC: OFF.
- **LLC:** 60 MB per socket, non-inclusive. SF inclusive of L1+L2.
  32 CHA tiles on socket 0 (uncore_cha_0 – uncore_cha_31 confirmed).
- **Memory:** ~503 GB on node 0. 8 DDR5 channels (uncore_imc_0–imc_7).
  2MB hugepages: 13312 (~26 GB) pre-allocated.
- **Kernel:** 6.8.0-79-generic, Ubuntu 22.04.5 LTS.

## PMU Events Confirmed

Key SF-related events confirmed present in `perf list`:
- `unc_cha_core_snp.evict_one` — primary SF eviction counter (H5)
- `unc_cha_core_snp.evict_gtone` — multi-core SF eviction
- `unc_cha_rxc_req_q1_retry.sf_victim` — SF capacity pressure
- `unc_cha_tor_inserts.ia_drd_pref` — prefetch fills to DRAM

Note: the event named `UNC_CHA_SF_EVICTION` in the mission brief does not
exist by that name; the semantic equivalent is `unc_cha_core_snp.evict_one`.
Documented in NEGATIVE_RESULTS.md §N1.

## Anomalies Observed

1. **N0**: Machine in default boot state — turbo on, governor=powersave,
   perf_event_paranoid=4, no MSR access. Setup.sh resolves all.
2. **N1**: `UNC_CHA_SF_EVICTION` event name mismatch — using
   `unc_cha_core_snp.evict_one` instead.
3. **N2**: 2-socket machine (not single-socket as spec'd) — all experiments
   scoped to socket 0. No impact on validity.
4. **N3**: No passwordless sudo — setup.sh requires interactive password once.

## Gate Conditions for Phase 1

validate.sh must exit 0. The failing conditions are:
- [ ] perf_event_paranoid ≤ 0
- [ ] Turbo: disabled
- [ ] Governor: performance
- [ ] Frequency: locked (min=max ≈ 3000 MHz)
- [ ] NUMA balancing: disabled
- [ ] rdmsr: installed
- [ ] /dev/cpu/*/msr: accessible by user domin
- [ ] Uncore perf read: functional

**Required action:** `sudo env/setup.sh`

## Next Phase Readiness

Phase 0: COMPLETE.
Phase 1: BLOCKED pending `sudo env/setup.sh && env/validate.sh`.

After setup.sh runs successfully:
1. Run `env/validate.sh` — must exit 0.
2. Compile benchmarks: `make -C bench/`.
3. Run Phase 1 calibration: `python3 exp/01_calibration.py`.
