# Phase 5-NEW — SNC Isolation Control Check
## Date: 2026-05-10T10:00:15.425978

## SNC Status

- NUMA nodes detected: 2
- SNC mode inference: **SNC_OFF_2SOCKET**

### numactl --hardware output

```
available: 2 nodes (0-1)
node 0 cpus: 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 64 65 66 67 68 69 70 71 72 73 74 75 76 77 78 79 80 81 82 83 84 85 86 87 88 89 90 91 92 93 94 95
node 0 size: 515666 MB
node 0 free: 307233 MB
node 1 cpus: 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 96 97 98 99 100 101 102 103 104 105 106 107 108 109 110 111 112 113 114 115 116 117 118 119 120 121 122 123 124 125 126 127
node 1 size: 516023 MB
node 1 free: 474538 MB
node distances:
node   0   1 
  0:  10  21 
  1:  21  10 
```

## H10 Evaluation

**H10: N/A — SNC is not enabled on this platform.**

SNC (Sub-NUMA Clustering) was confirmed disabled:
- 2 NUMA nodes present, one per socket (not 4 sub-clusters per socket)
- Node distances: 10 (intra-socket), 21 (inter-socket)

This matches the finding documented in NEGATIVE_RESULTS.md §N2.

**Impact on paper:** The SNC isolation control (H10) cannot be evaluated
without a BIOS change and reboot. Document as a platform limitation.

**Alternative approach (partial):** Run victim on CPU 0 (node 0) with
aggressors on node 1 CPUs. This tests cross-socket SF isolation, which
is stronger than the proposed SNC test but confounds NUMA access latency.
Not recommended as a primary control; leave H10 as N/A.

**Recommendation for paper:**
  - Acknowledge SNC-OFF as a limitation in §Implementation.
  - Note that SNC isolation would strengthen the SF-locality claim.
  - The L2-fit control (Phase 4-NEW) partially compensates: if tax persists
    at L2 level, the mechanism must involve back-invalidation traversing the
    interconnect between cores, which is the SF-mediated pathway.
