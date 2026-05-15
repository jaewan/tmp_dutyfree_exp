#!/usr/bin/env python3
import csv, os, re, statistics
from collections import defaultdict
from pathlib import Path

LOG = Path(os.environ.get('LOG_PATH','/home/domin/CoherenceTest/APNET/results/hotos_20260306/phase2_table3_isobw_final/phase2_table3_isobw_final.log'))
OUT = LOG.parent

re_b0 = re.compile(r'^=== BASELINE profile=(\S+) placement=(\S+) run=(\d+) vcore=(\d+) vnode=(\d+) START ===$')
re_br = re.compile(r'^=== BASELINE_RC profile=(\S+) placement=(\S+) run=(\d+) rc=(\d+) ===$')
re_c0 = re.compile(r'^=== CORUN profile=(\S+) placement=(\S+) run=(\d+) mode=(\S+) threads=(\d+) vcore=(\d+) vnode=(\d+) cores=([^ ]+) START ===$')
re_cr = re.compile(r'^=== CORUN_RC profile=(\S+) placement=(\S+) run=(\d+) mode=(\S+) victim_rc=(\d+) aggressor_rc=(\d+) ===$')
re_v  = re.compile(r'^VICTIM core=(\d+) ipc=([0-9.]+) l2_miss_rate=([0-9.]+) cycles=(\d+) insns=(\d+) l2_hit=(\d+) l2_miss=(\d+) iters=(\d+) sec=([0-9.]+)')
re_bw = re.compile(r'^RESULT mode=(\S+) threads=(\d+) bw_gbps=([0-9.]+)')

rows=[]; cur=None
for ln in LOG.read_text(errors='ignore').splitlines():
    m=re_b0.match(ln)
    if m:
        p,pl,r,vc,vn=m.groups(); cur={'type':'baseline','profile':p,'placement':pl,'run':int(r),'vcore':int(vc),'vnode':int(vn)}; continue
    m=re_c0.match(ln)
    if m:
        p,pl,r,mode,th,vc,vn,cores=m.groups(); cur={'type':'corun','profile':p,'placement':pl,'run':int(r),'mode':mode,'threads':int(th),'vcore':int(vc),'vnode':int(vn),'cores':cores}; continue
    m=re_v.match(ln)
    if m and cur is not None:
        _,ipc,miss,cyc,_,_,_,iters,_=m.groups(); cur.update({'ipc':float(ipc),'l2_miss_rate':float(miss),'cycles':int(cyc),'iters':int(iters),'cyc_per_iter':int(cyc)/max(int(iters),1)}); continue
    m=re_bw.match(ln)
    if m and cur is not None and cur.get('type')=='corun':
        cur['bw_gbps']=float(m.group(3)); continue
    m=re_br.match(ln)
    if m and cur is not None and cur.get('type')=='baseline':
        rows.append(cur); cur=None; continue
    m=re_cr.match(ln)
    if m and cur is not None and cur.get('type')=='corun':
        rows.append(cur); cur=None; continue

raw=OUT/'phase2_table3_isobw_raw.csv'
with raw.open('w',newline='') as f:
    fn=['type','profile','placement','run','mode','threads','bw_gbps','ipc','l2_miss_rate','cycles','iters','cyc_per_iter']
    w=csv.DictWriter(f,fieldnames=fn); w.writeheader(); [w.writerow({k:r.get(k,'') for k in fn}) for r in rows]

base={(r['profile'],r['placement'],r['run']):r for r in rows if r['type']=='baseline'}
norm=[]
for r in rows:
    if r['type']!='corun': continue
    b=base[(r['profile'],r['placement'],r['run'])]
    x=dict(r); x['delta_ipc_pct']=100*(r['ipc']-b['ipc'])/b['ipc']; x['delta_cpi_pct']=100*(r['cyc_per_iter']-b['cyc_per_iter'])/b['cyc_per_iter']; norm.append(x)

normf=OUT/'phase2_table3_isobw_norm.csv'
with normf.open('w',newline='') as f:
    fn=['profile','placement','run','mode','threads','bw_gbps','delta_ipc_pct','delta_cpi_pct']
    w=csv.DictWriter(f,fieldnames=fn); w.writeheader(); [w.writerow({k:r.get(k,'') for k in fn}) for r in norm]

def ms(vals):
    vals=list(vals); return statistics.mean(vals), (statistics.stdev(vals) if len(vals)>1 else 0.0), len(vals)

g=defaultdict(list)
for r in norm: g[(r['profile'],r['placement'],r['mode'],r['threads'])].append(r)
sumf=OUT/'phase2_table3_isobw_summary.csv'
with sumf.open('w',newline='') as f:
    w=csv.writer(f); w.writerow(['profile','placement','mode','threads','n','bw_mean','bw_sd','delta_ipc_mean_pct','delta_ipc_sd_pct','delta_cpi_mean_pct','delta_cpi_sd_pct'])
    for k in sorted(g):
        rs=g[k]; bwm,bws,n=ms(r['bw_gbps'] for r in rs); dim,dis,_=ms(r['delta_ipc_pct'] for r in rs); dcm,dcs,_=ms(r['delta_cpi_pct'] for r in rs)
        w.writerow([k[0],k[1],k[2],k[3],n,f'{bwm:.3f}',f'{bws:.3f}',f'{dim:.3f}',f'{dis:.3f}',f'{dcm:.3f}',f'{dcs:.3f}'])

pairs=[]
for p in sorted(set(r['profile'] for r in norm)):
  for pl in sorted(set(r['placement'] for r in norm if r['profile']==p)):
    wb={r['run']:r for r in norm if r['profile']==p and r['placement']==pl and r['mode']=='wb_load'}
    wc={r['run']:r for r in norm if r['profile']==p and r['placement']==pl and r['mode']=='wc_ntdqa'}
    common=sorted(set(wb)&set(wc))
    di=[wb[r]['delta_ipc_pct']-wc[r]['delta_ipc_pct'] for r in common]
    dc=[wb[r]['delta_cpi_pct']-wc[r]['delta_cpi_pct'] for r in common]
    db=[wb[r]['bw_gbps']-wc[r]['bw_gbps'] for r in common]
    pairs.append({'profile':p,'placement':pl,'n':len(common),'wb_minus_wc_bw_mean_gbps':statistics.mean(db),'wb_minus_wc_bw_sd_gbps':statistics.stdev(db) if len(db)>1 else 0.0,'wb_minus_wc_delta_ipc_mean_pct':statistics.mean(di),'wb_minus_wc_delta_ipc_sd_pct':statistics.stdev(di) if len(di)>1 else 0.0,'wb_minus_wc_delta_cpi_mean_pct':statistics.mean(dc),'wb_minus_wc_delta_cpi_sd_pct':statistics.stdev(dc) if len(dc)>1 else 0.0})

pf=OUT/'phase2_table3_isobw_paired.csv'
with pf.open('w',newline='') as f:
    fn=list(pairs[0].keys())
    w=csv.DictWriter(f,fieldnames=fn); w.writeheader()
    for r in pairs:
        rr={k:(f'{v:.3f}' if isinstance(v,float) else v) for k,v in r.items()}; w.writerow(rr)

ready=OUT/'table3_isobw_ready.csv'
summary=list(csv.DictReader(sumf.open())); paired=list(csv.DictReader(pf.open()))
with ready.open('w',newline='') as f:
    w=csv.writer(f)
    w.writerow(['placement','wb_threads','wc_threads','bw_wb_mean','bw_wc_mean','l2hot_wb_delta_ipc_pct','chase_wb_delta_cpi_pct','chase_wc_delta_cpi_pct','chase_wb_minus_wc_delta_cpi_pct'])
    for pl in ['A_same_ccd','B_diff_ccd_same_socket','C_diff_socket','D_both_sockets']:
        l2_wb=[r for r in summary if r['profile']=='l2hot' and r['placement']==pl and r['mode']=='wb_load'][0]
        ch_wb=[r for r in summary if r['profile']=='chase' and r['placement']==pl and r['mode']=='wb_load'][0]
        ch_wc=[r for r in summary if r['profile']=='chase' and r['placement']==pl and r['mode']=='wc_ntdqa'][0]
        ch_p=[r for r in paired if r['profile']=='chase' and r['placement']==pl][0]
        w.writerow([pl,l2_wb['threads'],ch_wc['threads'],l2_wb['bw_mean'],ch_wc['bw_mean'],l2_wb['delta_ipc_mean_pct'],ch_wb['delta_cpi_mean_pct'],ch_wc['delta_cpi_mean_pct'],ch_p['wb_minus_wc_delta_cpi_mean_pct']])

print('rows',len(rows),'norm',len(norm))
print('ready',ready)
