# Paper Claim Validation Summary

| claim | metric | mean | sd | n | unit | context |
|---|---:|---:|---:|---:|---|---|
| cat_bw | no_cat | 24.800200 | 0.039182 | 5 | GB/s | wb_load |
| cat_bw | with_cat | 24.807000 | 0.023420 | 5 | GB/s | wb_load |
| cat_degradation | no_cat | -0.026143 | 0.092748 | 5 | pct | l2hot_ipc_drop |
| cat_degradation | with_cat | 0.022091 | 0.020578 | 5 | pct | l2hot_ipc_drop |
| pmu_bw | wb_load | 24.802000 | 0.036647 | 5 | GB/s | chase |
| pmu_bw | wc_ntdqa | 24.793200 | 0.017866 | 5 | GB/s | chase |
| pmu_event | wb_load | 106367.000000 | 8214.750574 | 5 | count | amd_df/event=0x07 |
| pmu_event | wb_load | 6275901941.200000 | 35207366.520890 | 5 | count | amd_l3/event=0x04 |
| pmu_event | wc_ntdqa | 180617.400000 | 73695.998923 | 5 | count | amd_df/event=0x07 |
| pmu_event | wc_ntdqa | 1115543991.400000 | 21426726.824946 | 5 | count | amd_l3/event=0x04 |
| pmu_victim_cpi | wb_load | 4653865.552129 | 6158.103566 | 5 | cycles/iter | chase |
| pmu_victim_cpi | wc_ntdqa | 3658662.644697 | 56550.848112 | 5 | cycles/iter | chase |
| prefetch_bw | wb_load | 15.764600 | 0.005030 | 5 | GB/s | 1t |
| prefetch_bw | wb_prefetchnta | 15.677000 | 0.003391 | 5 | GB/s | 1t |
| prefetch_bw | wc_ntdqa | 4.173800 | 0.001304 | 5 | GB/s | 1t |
| prefetch_victim_bw | wb_load | 24.814800 | 0.024222 | 5 | GB/s | l2hot |
| prefetch_victim_bw | wb_prefetchnta | 24.879600 | 0.039278 | 5 | GB/s | l2hot |
| prefetch_victim_bw | wc_ntdqa | 24.828400 | 0.015027 | 5 | GB/s | l2hot |
| prefetch_victim_delta | wb_load | -0.022141 | 0.106871 | 5 | pct | l2hot_ipc_drop |
| prefetch_victim_delta | wb_prefetchnta | -0.046228 | 0.077386 | 5 | pct | l2hot_ipc_drop |
| prefetch_victim_delta | wc_ntdqa | 0.010027 | 0.068111 | 5 | pct | l2hot_ipc_drop |
| rdt_proxy_bw | baseline | 24.841800 | 0.041722 | 5 | GB/s | wb8 |
| rdt_proxy_bw | oneway | 24.780000 | 0.046589 | 5 | GB/s | wb8 |
| rdt_proxy_drop | oneway_vs_baseline | 0.248493 | 0.277811 | 5 | pct | wb8 |
