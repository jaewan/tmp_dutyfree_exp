# Results Ledger

- PHASE: PRECHECK
- CMD: ps ... | rg bin/(victim|aggressor|intra_app_corun)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/stale_processes.txt
- FINDING: captured stale-process snapshot
- GATE_STATUS: PASS

- PHASE: PRECHECK
- CMD: lscpu/numactl topology snapshot
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/topology_snapshot.txt
- FINDING: captured current topology/core mapping
- GATE_STATUS: PASS

- PHASE: PRECHECK
- CMD: make -C /home/domin/CoherenceTest/APNET -j4
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/build.log
- FINDING: build succeeded
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=1 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=2 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=3 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=4 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=5 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=6 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=7 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=8 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=9 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_A
- CMD: run=10 baseline+paired(WB/WC randomized)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expA
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_B
- CMD: run=1 randomized(baseline,columnar_wb)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expB
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_B
- CMD: run=2 randomized(baseline,columnar_wb)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expB
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_B
- CMD: run=3 randomized(baseline,columnar_wb)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expB
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_B
- CMD: run=4 randomized(baseline,columnar_wb)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expB
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: EXP_B
- CMD: run=5 randomized(baseline,columnar_wb)
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/raw/expB
- FINDING: completed run with deterministic order
- GATE_STATUS: PASS

- PHASE: ANALYSIS
- CMD: python3 summarize all_runs.csv
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143
- FINDING: generated experimentA_summary.csv, experimentB_summary.csv, paired_stats.csv
- GATE_STATUS: PASS

- PHASE: SUITE
- CMD: results/20260308_180143/scripts/run_additional_experiments.sh
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_180143/run_additional_experiments.log
- FINDING: completed Experiment A and B
- GATE_STATUS: PASS

