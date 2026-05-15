# Phase 17.3 — SF Saturation Full Matrix Summary
## Generated: 2026-05-13T02:42:40.720408

- Phase 17.1 calibration SF rate (R32, 10s): 220395/s
- Phase 17.2 proxy SF rate (S32, 10s): 199730/s
- Total trials: 240

## Per-Cell Summary

| Cond | WSS | n | Cyc/load | SF/s | SF_victim/s | L3/s | L2_hit | BW GB/s |
|------|-----|---|----------|------|-------------|------|--------|--------|
| Q | 384KB | 30 | 18.4 | 88760 | 0 | 2954 | 100.0% | 0.00 |
| Q | 32MB | 30 | 81.5 | 36575 | 0 | 12859 | 97.2% | 0.00 |
| R16 | 384KB | 30 | 18.2 | 52098 | 0 | 1000 | 100.0% | 0.00 |
| R16 | 32MB | 30 | 81.6 | 17312 | 0 | 36366 | 92.3% | 0.00 |
| R24 | 384KB | 30 | 18.2 | 17388 | 0 | 626 | 100.0% | 0.00 |
| R24 | 32MB | 30 | 81.6 | 25630 | 0 | 37741 | 92.0% | 0.00 |
| R32 | 384KB | 30 | 18.1 | 20791 | 0 | 2350 | 100.0% | 0.00 |
| R32 | 32MB | 30 | 81.4 | 47205 | 0 | 17162 | 96.1% | 0.00 |

## H13 Preliminary Check

R32 @ 384KB: SF eviction rate = 20791/s (0.5× baseline)
H13 threshold: ≥ 450000/s (10× baseline)
Preliminary verdict: LIKELY FAIL — run exp/17_analysis.py for full test

Full statistical analysis: `python3 exp/17_analysis.py`
