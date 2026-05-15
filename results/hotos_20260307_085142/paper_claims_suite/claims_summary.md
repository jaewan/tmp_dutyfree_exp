# Paper Claim Validation Summary

| claim | metric | mean | sd | n | unit | context |
|---|---:|---:|---:|---:|---|---|
| cat_bw | no_cat | 24.820400 | 0.042330 | 5 | GB/s | wb_load |
| cat_bw | with_cat | 24.823000 | 0.051711 | 5 | GB/s | wb_load |
| cat_degradation | no_cat | -0.030134 | 0.057247 | 5 | pct | l2hot_ipc_drop |
| cat_degradation | with_cat | -0.018080 | 0.049920 | 5 | pct | l2hot_ipc_drop |
| pmu_bw | wb_load | 24.841600 | 0.044674 | 5 | GB/s | chase |
| pmu_bw | wc_ntdqa | 24.809400 | 0.014206 | 5 | GB/s | chase |
| pmu_event | wb_load | 107514.000000 | 28035.598005 | 5 | count | amd_df/event=0x07 |
| pmu_event | wb_load | 6291072530.800000 | 39184704.767229 | 5 | count | amd_l3/event=0x04 |
| pmu_event | wc_ntdqa | 235545.600000 | 90592.957206 | 5 | count | amd_df/event=0x07 |
| pmu_event | wc_ntdqa | 1113957284.800000 | 12621500.581649 | 5 | count | amd_l3/event=0x04 |
| pmu_victim_cpi | wb_load | 4653081.005852 | 10362.427072 | 5 | cycles/iter | chase |
| pmu_victim_cpi | wc_ntdqa | 3690311.287266 | 32667.121056 | 5 | cycles/iter | chase |
| prefetch_bw | wb_load | 15.769400 | 0.001817 | 5 | GB/s | 1t |
| prefetch_bw | wb_prefetchnta | 15.679400 | 0.005771 | 5 | GB/s | 1t |
| prefetch_bw | wc_ntdqa | 4.174800 | 0.000837 | 5 | GB/s | 1t |
| prefetch_victim_bw | wb_load | 24.810000 | 0.026239 | 5 | GB/s | l2hot |
| prefetch_victim_bw | wb_prefetchnta | 24.862400 | 0.037885 | 5 | GB/s | l2hot |
| prefetch_victim_bw | wc_ntdqa | 24.811400 | 0.018379 | 5 | GB/s | l2hot |
| prefetch_victim_delta | wb_load | -0.090488 | 0.082095 | 5 | pct | l2hot_ipc_drop |
| prefetch_victim_delta | wb_prefetchnta | -0.034263 | 0.124916 | 5 | pct | l2hot_ipc_drop |
| prefetch_victim_delta | wc_ntdqa | -0.018217 | 0.166691 | 5 | pct | l2hot_ipc_drop |
| rdt_proxy_bw | baseline | 24.854400 | 0.026283 | 5 | GB/s | wb8 |
| rdt_proxy_bw | oneway | 24.820400 | 0.021385 | 5 | GB/s | wb8 |
| rdt_proxy_drop | oneway_vs_baseline | 0.136708 | 0.135457 | 5 | pct | wb8 |
