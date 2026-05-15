- PHASE: PREFETCH
- CMD: scripts/run_paper_claims_suite.sh prefetch
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260308_145043/paper_claims_suite/prefetch
- FINDING: completed throughput + victim-impact checks
- GATE_STATUS: PASS

- PHASE: CAT
- CMD: scripts/run_paper_claims_suite.sh cat
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260308_145043/paper_claims_suite/cat
- FINDING: completed paired no_cat vs with_cat runs
- GATE_STATUS: PASS

- PHASE: RDT_PROXY
- CMD: scripts/run_paper_claims_suite.sh rdt_proxy
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260308_145043/paper_claims_suite/rdt_proxy
- FINDING: completed baseline vs one-way throughput retention runs
- GATE_STATUS: PASS

- PHASE: PMU
- CMD: scripts/run_paper_claims_suite.sh pmu
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260308_145043/paper_claims_suite/pmu
- FINDING: completed paired WB vs WC PMU runs at high BW
- GATE_STATUS: PASS

- PHASE: SUITE
- CMD: scripts/run_paper_claims_suite.sh
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260308_145043/paper_claims_suite
- FINDING: all claim phases completed; see claims_summary.csv/md
- GATE_STATUS: PASS

