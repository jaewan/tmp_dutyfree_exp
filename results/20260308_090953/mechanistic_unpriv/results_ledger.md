- PHASE: PREFETCH_BW
- CMD: results/20260308_090953/scripts/run_mechanistic_unpriv.sh prefetch_bw
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: wb_load/wb_prefetchnta/wc_ntdqa 1-thread throughput complete
- GATE_STATUS: PASS

- PHASE: PMU_PAIR
- CMD: run=1 randomized wb_load/wc_ntdqa
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: completed paired PMU run
- GATE_STATUS: PASS

- PHASE: PMU_PAIR
- CMD: run=2 randomized wb_load/wc_ntdqa
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: completed paired PMU run
- GATE_STATUS: PASS

- PHASE: PMU_PAIR
- CMD: run=3 randomized wb_load/wc_ntdqa
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: completed paired PMU run
- GATE_STATUS: PASS

- PHASE: PMU_PAIR
- CMD: run=4 randomized wb_load/wc_ntdqa
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: completed paired PMU run
- GATE_STATUS: PASS

- PHASE: PMU_PAIR
- CMD: run=5 randomized wb_load/wc_ntdqa
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: completed paired PMU run
- GATE_STATUS: PASS

- PHASE: MECHANISTIC_UNPRIV
- CMD: results/20260308_090953/scripts/run_mechanistic_unpriv.sh
- STDOUT: /home/domin/CoherenceTest/APNET/results/20260308_090953/mechanistic_unpriv/run_mechanistic_unpriv.log
- FINDING: all unprivileged phases completed
- GATE_STATUS: PASS

