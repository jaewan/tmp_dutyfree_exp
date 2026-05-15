# Command Manifest (Resolved Values)

## Global controls
- victim core/node: `128` / `1`
- victim working set: `4096 KB` pointer chase
- aggressor cores pool: `136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231`
- seed: `20260308`

## Experiment A (single-process, matched BW WB vs WC)
- WB cores (2T): `136,137`
- WC cores (9T): `136,137,138,139,140,141,142,143,224`
- Warmup/measure: `5s/15s`
- Runs: `10`

Exact command templates used:

```bash
# baseline
bin/intra_app_corun -c 128 -n 1 -v 4096 -W 5 -d 15 -S <seed> -m none

# WB scenario
bin/intra_app_corun -c 128 -n 1 -v 4096 -W 5 -d 15 -S <seed> \
  -m wb_load -t 2 -a 136,137 -s 256

# WC scenario
bin/intra_app_corun -c 128 -n 1 -v 4096 -W 5 -d 15 -S <seed> \
  -m wc_ntdqa -t 9 -a 136,137,138,139,140,141,142,143,224 -s 256
```

WB/WC order was randomized per run with deterministic seed and recorded in:
- `all_runs.csv` (`order_idx`, `scenario`)

## Experiment B (columnar scan proxy)
- Columnar WB cores (8T): `136,137,138,139,140,141,142,143`
- Warmup/measure: `5s/15s`
- Runs: `5`

Exact command templates used:

```bash
# baseline
bin/intra_app_corun -c 128 -n 1 -v 4096 -W 5 -d 15 -S <seed> -m none

# columnar proxy aggressor (WB)
bin/intra_app_corun -c 128 -n 1 -v 4096 -W 5 -d 15 -S <seed> \
  -m wb_column_scan -t 8 -a 136,137,138,139,140,141,142,143 -s 256
```

Baseline/columnar order was randomized per run with deterministic seed and recorded in:
- `all_runs.csv` (`order_idx`, `scenario`)
