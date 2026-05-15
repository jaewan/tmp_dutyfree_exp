# Phase 18.2 — Forced-Turnover SF Saturation Full Matrix Summary
## Generated: 2026-05-13T04:18:24.047253

- Phase 18.1 calibration SF rate (R32, 10s): 2353803/s
- Total trials: 300

## Per-Cell Summary

| Cond | WSS | n | Cyc/load | SF/s | L3/s | L2_hit | BW GB/s |
|------|-----|---|----------|------|------|--------|--------|
| Q | 384KB | 30 | 18.3 | 6747 | 1182 | 100.0% | 0.00 |
| Q | 32MB | 30 | 81.4 | 25413 | 9685 | 97.8% | 0.00 |
| R16 | 384KB | 30 | 18.2 | 202308 | 17453 | 100.0% | 0.00 |
| R16 | 32MB | 30 | 293.7 | 243841 | 8239462 | 5.4% | 0.00 |
| R24 | 384KB | 30 | 18.3 | 628901 | 46591 | 100.0% | 0.00 |
| R24 | 32MB | 30 | 296.6 | 612063 | 8226882 | 5.3% | 0.00 |
| R32 | 384KB | 30 | 18.8 | 3498536 | 56397 | 100.0% | 0.00 |
| R32 | 32MB | 30 | 300.5 | 2824927 | 8130515 | 5.2% | 0.00 |
| R8 | 384KB | 30 | 18.2 | 8438 | 900 | 100.0% | 0.00 |
| R8 | 32MB | 30 | 110.4 | 144960 | 3128679 | 13.0% | 0.00 |

## H16 Preliminary Check

R32 @ 384KB: SF eviction rate = 3498536/s (77.7x baseline)
H16 threshold: >= 450000/s (10x baseline)
Preliminary verdict: LIKELY PASS — run exp/18_analysis.py for full test

Full statistical analysis: `python3 exp/18_analysis.py`
