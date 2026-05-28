# Post-Setup Environment Snapshot
## 2026-05-28T18:06:52+09:00

| Setting | Value |
|---------|-------|
| perf_event_paranoid | -1 |
| numa_balancing | 0 |
| randomize_va_space | 1 |
| no_turbo | 1 |
| cpu0 governor | performance |
| cpu0 min_freq kHz | 2800000 |
| cpu0 max_freq kHz | 2800000 |
| cpu0 cur_freq kHz | 2800024 |
| MSR 0x1A4 cpu0 baseline | 20 |
| node0 2M hugepages | 24576 |
| node0 free 2M hugepages | 24576 |
| node2 present | YES |
| node2 2M hugepages | 24576 |
| node2 free 2M hugepages | 24576 |
| stream_wb_nopf caps | /home/jb/tmp_dutyfree_exp/intel_experiments/bench/aggressor/stream_wb_nopf cap_sys_rawio=ep |
| pointer_chase caps | /home/jb/tmp_dutyfree_exp/intel_experiments/bench/victim/pointer_chase cap_sys_rawio=ep |

## MSR 0x1A4 Baseline
```
# Baseline MSR 0x1A4 per core — captured 2026-05-28T18:06:50+09:00
cpu0  : 20
cpu1  : 20
cpu2  : 20
cpu3  : 20
cpu4  : 20
cpu5  : 20
cpu6  : 20
cpu7  : 20
cpu8  : 20
cpu9  : 20
cpu10 : 20
cpu11 : 20
cpu12 : 20
cpu13 : 20
cpu14 : 20
cpu15 : 20
cpu16 : 20
cpu17 : 20
cpu18 : 20
cpu19 : 20
cpu20 : 20
cpu21 : 20
cpu22 : 20
cpu23 : 20
cpu24 : 20
cpu25 : 20
cpu26 : 20
cpu27 : 20
cpu28 : 20
cpu29 : 20
cpu30 : 20
cpu31 : 20
```
