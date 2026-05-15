#!/usr/bin/env python3
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

from scipy.stats import ttest_rel

LOG = Path(os.environ.get("LOG_PATH", "results/hotos_20260306/enhanced_phase2/phase2_enhanced.log"))

re_base = re.compile(r"^=== BASELINE profile=(\S+) pair=(\S+) run=(\d+) tag=(pre|post) ===$")
re_corun = re.compile(r"^=== CORUN profile=(\S+) pair=(\S+) run=(\d+) mode=(\S+) threads=(\d+) START ===$")
re_victim = re.compile(r"^VICTIM .* ipc=([0-9.]+) .* cycles=(\d+) .* iters=(\d+) sec=([0-9.]+)")
re_bw = re.compile(r"^RESULT mode=(\S+) threads=(\d+) bw_gbps=([0-9.]+)")

if not LOG.exists():
    raise SystemExit(f"missing log: {LOG}")

lines = LOG.read_text(errors="ignore").splitlines()

records = []
cur = None

for ln in lines:
    mb = re_base.match(ln)
    if mb:
        profile, pair, run, tag = mb.groups()
        cur = {"type": "baseline", "profile": profile, "pair": pair, "run": int(run), "tag": tag}
        continue
    mc = re_corun.match(ln)
    if mc:
        profile, pair, run, mode, thr = mc.groups()
        cur = {"type": "corun", "profile": profile, "pair": pair, "run": int(run), "mode": mode, "threads": int(thr)}
        continue
    mv = re_victim.match(ln)
    if mv and cur is not None:
        ipc, cyc, iters, sec = mv.groups()
        cur["ipc"] = float(ipc)
        cur["cycles"] = int(cyc)
        cur["iters"] = int(iters)
        cur["sec"] = float(sec)
        cur["cpi_iter"] = cur["cycles"] / max(cur["iters"], 1)
        continue
    mr = re_bw.match(ln)
    if mr and cur is not None and cur.get("type") == "corun":
        mode, thr, bw = mr.groups()
        cur["bw"] = float(bw)
        records.append(cur)
        cur = None
        continue
    if ln.startswith("=== BASELINE_RC") and cur is not None and cur.get("type") == "baseline":
        records.append(cur)
        cur = None

by_key = defaultdict(list)
for r in records:
    key = (r.get("profile"), r.get("pair"), r.get("run"))
    by_key[key].append(r)

rows = []
for key, rs in by_key.items():
    profile, pair, run = key
    bases = [x for x in rs if x["type"] == "baseline" and "cpi_iter" in x]
    if not bases:
        continue
    base_cpi = statistics.mean([b["cpi_iter"] for b in bases])
    base_ipc = statistics.mean([b["ipc"] for b in bases])
    for c in [x for x in rs if x["type"] == "corun" and "cpi_iter" in x and "bw" in x]:
        rows.append({
            "profile": profile,
            "pair": pair,
            "run": run,
            "mode": c["mode"],
            "threads": c["threads"],
            "bw": c["bw"],
            "ipc": c["ipc"],
            "cpi_iter": c["cpi_iter"],
            "base_cpi": base_cpi,
            "base_ipc": base_ipc,
            "cpi_delta_pct": 100.0 * (c["cpi_iter"] - base_cpi) / base_cpi,
            "ipc_delta_pct": 100.0 * (c["ipc"] - base_ipc) / base_ipc,
        })

def ci95(v):
    if len(v) < 2:
        return (math.nan, math.nan)
    m = statistics.mean(v)
    s = statistics.stdev(v)
    h = 1.96 * s / math.sqrt(len(v))
    return (m - h, m + h)

print("profile,pair,mode,threads,n,bw_mean,cpi_delta_mean_pct,ipc_delta_mean_pct,cpi_delta_ci95_low,cpi_delta_ci95_high")
grp = defaultdict(list)
for r in rows:
    grp[(r["profile"], r["pair"], r["mode"], r["threads"])].append(r)

for k in sorted(grp.keys()):
    vs = grp[k]
    cpi_d = [x["cpi_delta_pct"] for x in vs]
    ipc_d = [x["ipc_delta_pct"] for x in vs]
    bw = [x["bw"] for x in vs]
    lo, hi = ci95(cpi_d)
    print(f"{k[0]},{k[1]},{k[2]},{k[3]},{len(vs)},{statistics.mean(bw):.3f},{statistics.mean(cpi_d):.3f},{statistics.mean(ipc_d):.3f},{lo:.3f},{hi:.3f}")

print("\npaired_ttest_by_profile_pair (metric=cpi_delta_pct, wb vs wc)")
for profile in sorted(set(r["profile"] for r in rows)):
    for pair in sorted(set(r["pair"] for r in rows if r["profile"] == profile)):
        wb = {(r["run"]): r["cpi_delta_pct"] for r in rows if r["profile"] == profile and r["pair"] == pair and r["mode"] == "wb_load"}
        wc = {(r["run"]): r["cpi_delta_pct"] for r in rows if r["profile"] == profile and r["pair"] == pair and r["mode"] == "wc_ntdqa"}
        common = sorted(set(wb) & set(wc))
        if len(common) < 2:
            continue
        a = [wb[r] for r in common]
        b = [wc[r] for r in common]
        t = ttest_rel(a, b)
        print(f"profile={profile} pair={pair} n={len(common)} mean_wb={statistics.mean(a):.3f}% mean_wc={statistics.mean(b):.3f}% p={t.pvalue:.6g}")
