#!/usr/bin/env python3
import csv
import os
import re
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path('/home/domin/CoherenceTest/APNET')
LOG = Path(os.environ.get('LOG_PATH', ROOT / 'results/hotos_20260306/phase2_table3/phase2_table3.log'))
OUTDIR = LOG.parent

if not LOG.exists():
    raise SystemExit(f'missing log: {LOG}')

re_b0 = re.compile(r'^=== BASELINE profile=(\S+) placement=(\S+) run=(\d+) vcore=(\d+) vnode=(\d+) START ===$')
re_br = re.compile(r'^=== BASELINE_RC profile=(\S+) placement=(\S+) run=(\d+) rc=(\d+) ===$')
re_c0 = re.compile(r'^=== CORUN profile=(\S+) placement=(\S+) run=(\d+) mode=(\S+) threads=(\d+) vcore=(\d+) vnode=(\d+) cores=([^ ]+) START ===$')
re_cr = re.compile(r'^=== CORUN_RC profile=(\S+) placement=(\S+) run=(\d+) mode=(\S+) victim_rc=(\d+) aggressor_rc=(\d+) ===$')
re_v = re.compile(r'^VICTIM core=(\d+) ipc=([0-9.]+) l2_miss_rate=([0-9.]+) cycles=(\d+) insns=(\d+) l2_hit=(\d+) l2_miss=(\d+) iters=(\d+) sec=([0-9.]+)')
re_bw = re.compile(r'^RESULT mode=(\S+) threads=(\d+) bw_gbps=([0-9.]+)')

rows = []
cur = None
for ln in LOG.read_text(errors='ignore').splitlines():
    m = re_b0.match(ln)
    if m:
        p,pl,r,vc,vn = m.groups()
        cur = {'type':'baseline','profile':p,'placement':pl,'run':int(r),'vcore':int(vc),'vnode':int(vn)}
        continue
    m = re_c0.match(ln)
    if m:
        p,pl,r,mode,th,vc,vn,cores = m.groups()
        cur = {'type':'corun','profile':p,'placement':pl,'run':int(r),'mode':mode,'threads':int(th),'vcore':int(vc),'vnode':int(vn),'cores':cores}
        continue
    m = re_v.match(ln)
    if m and cur is not None:
        core, ipc, miss, cyc, ins, l2h, l2m, iters, sec = m.groups()
        cur.update({'ipc':float(ipc),'l2_miss_rate':float(miss),'cycles':int(cyc),'iters':int(iters),'cyc_per_iter':int(cyc)/max(int(iters),1)})
        continue
    m = re_bw.match(ln)
    if m and cur is not None and cur.get('type')=='corun':
        mode, th, bw = m.groups()
        cur['bw_gbps'] = float(bw)
        continue
    m = re_br.match(ln)
    if m and cur is not None and cur.get('type')=='baseline':
        cur['rc'] = int(m.group(4)); rows.append(cur); cur = None
        continue
    m = re_cr.match(ln)
    if m and cur is not None and cur.get('type')=='corun':
        cur['victim_rc'] = int(m.group(5)); cur['aggressor_rc'] = int(m.group(6)); rows.append(cur); cur = None
        continue

if not rows:
    raise SystemExit('no rows parsed')

raw_csv = OUTDIR / 'phase2_table3_raw.csv'
with raw_csv.open('w', newline='') as f:
    fns = ['type','profile','placement','run','mode','threads','vcore','vnode','cores','bw_gbps','ipc','l2_miss_rate','cycles','iters','cyc_per_iter','rc','victim_rc','aggressor_rc']
    w = csv.DictWriter(f, fieldnames=fns); w.writeheader()
    for r in rows:
        w.writerow({k:r.get(k,'') for k in fns})

# normalize vs per-(profile,placement,run) baseline
base = {}
for r in rows:
    if r['type']=='baseline':
        base[(r['profile'],r['placement'],r['run'])] = r

norm = []
for r in rows:
    if r['type']!='corun':
        continue
    b = base[(r['profile'],r['placement'],r['run'])]
    x = dict(r)
    x['base_ipc'] = b['ipc']
    x['base_cpi'] = b['cyc_per_iter']
    x['delta_ipc_pct'] = 100.0*(r['ipc']-b['ipc'])/b['ipc']
    x['delta_cpi_pct'] = 100.0*(r['cyc_per_iter']-b['cyc_per_iter'])/b['cyc_per_iter']
    norm.append(x)

norm_csv = OUTDIR / 'phase2_table3_norm.csv'
with norm_csv.open('w', newline='') as f:
    fns = ['profile','placement','run','mode','threads','bw_gbps','ipc','l2_miss_rate','cyc_per_iter','base_ipc','base_cpi','delta_ipc_pct','delta_cpi_pct']
    w = csv.DictWriter(f, fieldnames=fns); w.writeheader()
    for r in norm:
        w.writerow({k:r.get(k,'') for k in fns})

def ms(v):
    v=list(v)
    return statistics.mean(v), (statistics.stdev(v) if len(v)>1 else 0.0), len(v)

grp = defaultdict(list)
for r in norm:
    grp[(r['profile'],r['placement'],r['mode'])].append(r)

sum_csv = OUTDIR / 'phase2_table3_summary.csv'
with sum_csv.open('w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['profile','placement','mode','n','bw_mean','bw_sd','delta_ipc_mean_pct','delta_ipc_sd_pct','delta_cpi_mean_pct','delta_cpi_sd_pct'])
    for k in sorted(grp):
        rs = grp[k]
        bwm,bws,n = ms(r['bw_gbps'] for r in rs)
        dim,disd,_ = ms(r['delta_ipc_pct'] for r in rs)
        dcm,dcs,_ = ms(r['delta_cpi_pct'] for r in rs)
        w.writerow([k[0],k[1],k[2],n,f'{bwm:.3f}',f'{bws:.3f}',f'{dim:.3f}',f'{disd:.3f}',f'{dcm:.3f}',f'{dcs:.3f}'])

# paired WB-WC per placement/profile
pairs=[]
for profile in sorted({r['profile'] for r in norm}):
    for pl in sorted({r['placement'] for r in norm if r['profile']==profile}):
        wb = {r['run']:r for r in norm if r['profile']==profile and r['placement']==pl and r['mode']=='wb_load'}
        wc = {r['run']:r for r in norm if r['profile']==profile and r['placement']==pl and r['mode']=='wc_ntdqa'}
        common=sorted(set(wb)&set(wc))
        if not common: continue
        d_ipc=[wb[r]['delta_ipc_pct']-wc[r]['delta_ipc_pct'] for r in common]
        d_cpi=[wb[r]['delta_cpi_pct']-wc[r]['delta_cpi_pct'] for r in common]
        d_bw=[wb[r]['bw_gbps']-wc[r]['bw_gbps'] for r in common]
        pairs.append({
            'profile':profile,'placement':pl,'n':len(common),
            'wb_minus_wc_delta_ipc_mean_pct':statistics.mean(d_ipc),'wb_minus_wc_delta_ipc_sd_pct':statistics.stdev(d_ipc) if len(d_ipc)>1 else 0.0,
            'wb_minus_wc_delta_cpi_mean_pct':statistics.mean(d_cpi),'wb_minus_wc_delta_cpi_sd_pct':statistics.stdev(d_cpi) if len(d_cpi)>1 else 0.0,
            'wb_minus_wc_bw_mean_gbps':statistics.mean(d_bw),'wb_minus_wc_bw_sd_gbps':statistics.stdev(d_bw) if len(d_bw)>1 else 0.0,
        })

paired_csv = OUTDIR / 'phase2_table3_paired.csv'
with paired_csv.open('w', newline='') as f:
    fn=['profile','placement','n','wb_minus_wc_delta_ipc_mean_pct','wb_minus_wc_delta_ipc_sd_pct','wb_minus_wc_delta_cpi_mean_pct','wb_minus_wc_delta_cpi_sd_pct','wb_minus_wc_bw_mean_gbps','wb_minus_wc_bw_sd_gbps']
    w=csv.DictWriter(f, fieldnames=fn); w.writeheader()
    for r in pairs:
        rr={k:(f'{v:.3f}' if isinstance(v,float) else v) for k,v in r.items()}
        w.writerow(rr)

# Table3-ready compact
ready_csv = OUTDIR / 'table3_ready.csv'
with ready_csv.open('w', newline='') as f:
    w=csv.writer(f)
    w.writerow(['placement','wb_delta_ipc_l2hot_mean_sd_pct','wb_delta_cpi_chase_mean_sd_pct','wc_delta_cpi_chase_mean_sd_pct','wb_minus_wc_delta_cpi_chase_mean_sd_pct'])
    summary=list(csv.DictReader(sum_csv.open()))
    paired=list(csv.DictReader(paired_csv.open()))
    placements=['A_same_ccd','B_diff_ccd_same_socket','C_diff_socket','D_both_sockets']
    for pl in placements:
        l2_wb=[r for r in summary if r['profile']=='l2hot' and r['placement']==pl and r['mode']=='wb_load'][0]
        ch_wb=[r for r in summary if r['profile']=='chase' and r['placement']==pl and r['mode']=='wb_load'][0]
        ch_wc=[r for r in summary if r['profile']=='chase' and r['placement']==pl and r['mode']=='wc_ntdqa'][0]
        ch_pair=[r for r in paired if r['profile']=='chase' and r['placement']==pl][0]
        w.writerow([
            pl,
            f"{l2_wb['delta_ipc_mean_pct']} +- {l2_wb['delta_ipc_sd_pct']}",
            f"{ch_wb['delta_cpi_mean_pct']} +- {ch_wb['delta_cpi_sd_pct']}",
            f"{ch_wc['delta_cpi_mean_pct']} +- {ch_wc['delta_cpi_sd_pct']}",
            f"{ch_pair['wb_minus_wc_delta_cpi_mean_pct']} +- {ch_pair['wb_minus_wc_delta_cpi_sd_pct']}",
        ])

print(f'rows={len(rows)} norm={len(norm)}')
print(f'raw_csv={raw_csv}')
print(f'norm_csv={norm_csv}')
print(f'summary_csv={sum_csv}')
print(f'paired_csv={paired_csv}')
print(f'table3_ready={ready_csv}')
