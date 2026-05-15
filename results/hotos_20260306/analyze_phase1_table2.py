#!/usr/bin/env python3
import csv
import math
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path('/home/domin/CoherenceTest/APNET')
LOG = Path(os.environ.get('LOG_PATH', ROOT / 'results/hotos_20260306/phase1_table2/phase1_table2.log'))
OUTDIR = LOG.parent

if not LOG.exists():
    raise SystemExit(f'missing log: {LOG}')

re_start = re.compile(r'^=== SCENARIO profile=(\S+) run=(\d+) scenario=(\S+) mode=(\S+) threads=(\d+) band=(\S+) START ===$')
re_rc = re.compile(r'^=== SCENARIO_RC profile=(\S+) run=(\d+) scenario=(\S+) mode=(\S+) threads=(\d+) band=(\S+) victim_rc=(\d+) aggressor_rc=(\d+) ===$')
re_victim = re.compile(r'^VICTIM core=(\d+) ipc=([0-9.]+) l2_miss_rate=([0-9.]+) cycles=(\d+) insns=(\d+) l2_hit=(\d+) l2_miss=(\d+) iters=(\d+) sec=([0-9.]+)')
re_result = re.compile(r'^RESULT mode=(\S+) threads=(\d+) bw_gbps=([0-9.]+)')

rows = []
cur = None
for ln in LOG.read_text(errors='ignore').splitlines():
    m = re_start.match(ln)
    if m:
        profile, run, scen, mode, th, band = m.groups()
        cur = {
            'profile': profile,
            'run': int(run),
            'scenario': scen,
            'mode': mode,
            'threads': int(th),
            'band': band,
        }
        continue

    mv = re_victim.match(ln)
    if mv and cur is not None:
        _, ipc, miss, cyc, ins, l2h, l2m, iters, sec = mv.groups()
        cur.update({
            'ipc': float(ipc),
            'l2_miss_rate': float(miss),
            'cycles': int(cyc),
            'insns': int(ins),
            'l2_hit': int(l2h),
            'l2_miss': int(l2m),
            'iters': int(iters),
            'sec': float(sec),
            'cyc_per_iter': int(cyc) / max(int(iters), 1),
        })
        continue

    mr = re_result.match(ln)
    if mr and cur is not None:
        _, _, bw = mr.groups()
        cur['bw_gbps'] = float(bw)
        continue

    mc = re_rc.match(ln)
    if mc and cur is not None:
        profile, run, scen, mode, th, band, vrc, arc = mc.groups()
        cur['victim_rc'] = int(vrc)
        cur['aggressor_rc'] = int(arc)
        rows.append(cur)
        cur = None

if not rows:
    raise SystemExit('no parsed rows')

for r in rows:
    if r['scenario'] == 'baseline':
        r.setdefault('bw_gbps', 0.0)

raw_csv = OUTDIR / 'phase1_table2_raw.csv'
with raw_csv.open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=[
        'profile','run','scenario','band','mode','threads','bw_gbps',
        'ipc','l2_miss_rate','cycles','iters','cyc_per_iter','victim_rc','aggressor_rc'
    ])
    w.writeheader()
    for r in sorted(rows, key=lambda x: (x['profile'], x['run'], x['scenario'])):
        w.writerow({k: r.get(k, '') for k in w.fieldnames})

by_pr = defaultdict(list)
for r in rows:
    by_pr[(r['profile'], r['run'])].append(r)

norm_rows = []
for (profile, run), rs in sorted(by_pr.items()):
    baselines = [x for x in rs if x['scenario'] == 'baseline']
    if len(baselines) != 1:
        raise SystemExit(f'expected 1 baseline for profile={profile} run={run}, got {len(baselines)}')
    b = baselines[0]
    for r in rs:
        if r['scenario'] == 'baseline':
            continue
        out = dict(r)
        out['base_ipc'] = b['ipc']
        out['base_l2_miss_rate'] = b['l2_miss_rate']
        out['base_cyc_per_iter'] = b['cyc_per_iter']
        out['delta_ipc_pct'] = 100.0 * (r['ipc'] - b['ipc']) / b['ipc']
        out['delta_l2_miss_abs'] = r['l2_miss_rate'] - b['l2_miss_rate']
        out['delta_cpi_pct'] = 100.0 * (r['cyc_per_iter'] - b['cyc_per_iter']) / b['cyc_per_iter']
        norm_rows.append(out)

norm_csv = OUTDIR / 'phase1_table2_norm.csv'
with norm_csv.open('w', newline='') as f:
    fns = [
        'profile','run','scenario','band','mode','threads','bw_gbps','ipc','l2_miss_rate','cyc_per_iter',
        'base_ipc','base_l2_miss_rate','base_cyc_per_iter',
        'delta_ipc_pct','delta_l2_miss_abs','delta_cpi_pct'
    ]
    w = csv.DictWriter(f, fieldnames=fns)
    w.writeheader()
    for r in sorted(norm_rows, key=lambda x: (x['profile'], x['band'], x['mode'], x['run'])):
        w.writerow({k: r.get(k, '') for k in fns})

def mean_sd(vals):
    vals = list(vals)
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return m, s, len(vals)

g = defaultdict(list)
for r in norm_rows:
    g[(r['profile'], r['band'], r['mode'], r['threads'])].append(r)

sum_csv = OUTDIR / 'phase1_table2_summary.csv'
with sum_csv.open('w', newline='') as f:
    w = csv.writer(f)
    w.writerow([
        'profile','band','mode','threads','n',
        'bw_mean','bw_sd','ipc_mean','ipc_sd','l2_miss_mean','l2_miss_sd','cpi_mean','cpi_sd',
        'delta_ipc_mean_pct','delta_ipc_sd_pct','delta_cpi_mean_pct','delta_cpi_sd_pct','delta_l2_miss_mean_abs','delta_l2_miss_sd_abs'
    ])
    for k in sorted(g.keys()):
        rs = g[k]
        bw_m,bw_s,n = mean_sd(r['bw_gbps'] for r in rs)
        ipc_m,ipc_s,_ = mean_sd(r['ipc'] for r in rs)
        l2_m,l2_s,_ = mean_sd(r['l2_miss_rate'] for r in rs)
        cpi_m,cpi_s,_ = mean_sd(r['cyc_per_iter'] for r in rs)
        dipc_m,dipc_s,_ = mean_sd(r['delta_ipc_pct'] for r in rs)
        dcpi_m,dcpi_s,_ = mean_sd(r['delta_cpi_pct'] for r in rs)
        dl2_m,dl2_s,_ = mean_sd(r['delta_l2_miss_abs'] for r in rs)
        w.writerow([k[0],k[1],k[2],k[3],n,
                    f'{bw_m:.3f}',f'{bw_s:.3f}',f'{ipc_m:.6f}',f'{ipc_s:.6f}',f'{l2_m:.4f}',f'{l2_s:.4f}',f'{cpi_m:.3f}',f'{cpi_s:.3f}',
                    f'{dipc_m:.3f}',f'{dipc_s:.3f}',f'{dcpi_m:.3f}',f'{dcpi_s:.3f}',f'{dl2_m:.4f}',f'{dl2_s:.4f}'])

# Paired WB vs WC deltas per profile/band by run
paired = []
for profile in sorted({r['profile'] for r in norm_rows}):
    for band in ['low','mid','high']:
        wb = {r['run']: r for r in norm_rows if r['profile']==profile and r['band']==band and r['mode']=='wb_load'}
        wc = {r['run']: r for r in norm_rows if r['profile']==profile and r['band']==band and r['mode']=='wc_ntdqa'}
        common = sorted(set(wb) & set(wc))
        if not common:
            continue
        d_ipc = [wb[r]['delta_ipc_pct'] - wc[r]['delta_ipc_pct'] for r in common]
        d_cpi = [wb[r]['delta_cpi_pct'] - wc[r]['delta_cpi_pct'] for r in common]
        d_bw = [wb[r]['bw_gbps'] - wc[r]['bw_gbps'] for r in common]
        paired.append({
            'profile': profile,
            'band': band,
            'n': len(common),
            'wb_minus_wc_delta_ipc_mean_pct': statistics.mean(d_ipc),
            'wb_minus_wc_delta_ipc_sd_pct': statistics.stdev(d_ipc) if len(d_ipc)>1 else 0.0,
            'wb_minus_wc_delta_cpi_mean_pct': statistics.mean(d_cpi),
            'wb_minus_wc_delta_cpi_sd_pct': statistics.stdev(d_cpi) if len(d_cpi)>1 else 0.0,
            'wb_minus_wc_bw_mean_gbps': statistics.mean(d_bw),
            'wb_minus_wc_bw_sd_gbps': statistics.stdev(d_bw) if len(d_bw)>1 else 0.0,
        })

paired_csv = OUTDIR / 'phase1_table2_paired.csv'
with paired_csv.open('w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(paired[0].keys()) if paired else [
        'profile','band','n','wb_minus_wc_delta_ipc_mean_pct','wb_minus_wc_delta_ipc_sd_pct',
        'wb_minus_wc_delta_cpi_mean_pct','wb_minus_wc_delta_cpi_sd_pct','wb_minus_wc_bw_mean_gbps','wb_minus_wc_bw_sd_gbps'
    ])
    w.writeheader()
    for r in paired:
        rr = dict(r)
        for k,v in list(rr.items()):
            if isinstance(v,float):
                rr[k] = f'{v:.3f}'
        w.writerow(rr)

# Table2-ready compact output
ready_csv = OUTDIR / 'table2_ready.csv'
with ready_csv.open('w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['band','mode','threads','bw_mean_sd_gbps','l2hot_delta_ipc_mean_sd_pct','chase_delta_cpi_mean_sd_pct'])
    for band in ['low','mid','high']:
        for mode in ['wc_ntdqa','wb_load']:
            k1 = ('l2hot', band, mode)
            k2 = ('chase', band, mode)
            r1 = [r for r in csv.DictReader(sum_csv.open()) if r['profile']=='l2hot' and r['band']==band and r['mode']==mode]
            r2 = [r for r in csv.DictReader(sum_csv.open()) if r['profile']=='chase' and r['band']==band and r['mode']==mode]
            if not r1 or not r2:
                continue
            a = r1[0]; b = r2[0]
            w.writerow([
                band,
                mode,
                a['threads'],
                f"{a['bw_mean']} +- {a['bw_sd']}",
                f"{a['delta_ipc_mean_pct']} +- {a['delta_ipc_sd_pct']}",
                f"{b['delta_cpi_mean_pct']} +- {b['delta_cpi_sd_pct']}",
            ])

print(f'parsed_rows={len(rows)} norm_rows={len(norm_rows)}')
print(f'raw_csv={raw_csv}')
print(f'norm_csv={norm_csv}')
print(f'summary_csv={sum_csv}')
print(f'paired_csv={paired_csv}')
print(f'table2_ready={ready_csv}')
