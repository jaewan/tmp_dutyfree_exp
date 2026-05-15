#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="$ROOT/results/hotos_20260306/phase2_table3_isobw_cal"
RAWDIR="$OUTDIR/raw"
CSV="$OUTDIR/calibration.csv"
SUM="$OUTDIR/calibration_summary.csv"
LOG="$OUTDIR/calibration.log"
LEDGER="$ROOT/results/hotos_20260306/results_ledger.md"
mkdir -p "$RAWDIR"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

A="137,138,139,140,141,142,143,393,394,395,396,397,398,399"
BC="136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231"
D="16,17,18,19,136,137,138,139,272,273,274,275,392,393,394,395"

WB_THREADS=(8)
WC_THREADS=(8 9 10 11 12)
REPS=3
DUR=15
SIZE=256

echo "placement,mode,threads,run,bw_gbps" > "$CSV"

run_one(){
  local placement="$1" mode="$2" th="$3" run="$4" cores="$5"
  local corelist outf bw rc
  corelist=$(echo "$cores" | tr ',' '\n' | head -"$th" | tr '\n' ',' | sed 's/,$//')
  outf="$RAWDIR/${placement}_${mode}_t${th}_r${run}.log"
  cmd=("$ROOT/bin/aggressor" -m "$mode" -t "$th" -c "$corelist" -s "$SIZE" -d "$DUR")
  echo "RUN placement=$placement mode=$mode th=$th run=$run cmd=${cmd[*]}"
  set +e
  timeout 45s "${cmd[@]}" > "$outf" 2>&1
  rc=$?
  set -e
  cat "$outf"
  if [[ $rc -ne 0 ]]; then
    echo "ERROR rc=$rc"
    exit 1
  fi
  bw=$(grep '^RESULT ' "$outf" | sed -E 's/.*bw_gbps=([0-9.]+).*/\1/' | tail -n1)
  [[ -n "$bw" ]]
  echo "$placement,$mode,$th,$run,$bw" >> "$CSV"
}

for p in A B C D; do
  case "$p" in
    A) cores="$A" ;;
    B) cores="$BC" ;;
    C) cores="$BC" ;;
    D) cores="$D" ;;
  esac

  for t in "${WB_THREADS[@]}"; do
    for r in 1 2 3; do
      run_one "$p" wb_load "$t" "$r" "$cores"
      sleep 1
    done
  done
  for t in "${WC_THREADS[@]}"; do
    for r in 1 2 3; do
      run_one "$p" wc_ntdqa "$t" "$r" "$cores"
      sleep 1
    done
  done
done

python3 - <<'PY' "$CSV" "$SUM"
import csv, statistics, sys
from collections import defaultdict
inp,out=sys.argv[1:3]
rows=defaultdict(list)
with open(inp) as f:
  for r in csv.DictReader(f):
    rows[(r['placement'],r['mode'],int(r['threads']))].append(float(r['bw_gbps']))
with open(out,'w',newline='') as f:
  w=csv.writer(f); w.writerow(['placement','mode','threads','mean_bw_gbps','sd_bw_gbps','n'])
  for k in sorted(rows):
    v=rows[k]
    w.writerow([k[0],k[1],k[2],f"{statistics.mean(v):.3f}",f"{statistics.stdev(v):.3f}",len(v)])
print('done')
PY

python3 - <<'PY' "$SUM"
import csv,sys
sumf=sys.argv[1]
by={}
with open(sumf) as f:
  for r in csv.DictReader(f):
    by[(r['placement'],r['mode'],int(r['threads']))]=float(r['mean_bw_gbps'])
for p in ['A','B','C','D']:
  wb=by[(p,'wb_load',8)]
  best=None
  for t in [8,9,10,11,12]:
    wc=by[(p,'wc_ntdqa',t)]
    d=abs(wc-wb)
    if best is None or d<best[0]: best=(d,t,wc)
  print(f'placement={p} wb8={wb:.3f} best_wc_t={best[1]} wc={best[2]:.3f} delta={best[2]-wb:+.3f}')
PY

cat >> "$LEDGER" <<'LEDGER_EOF'
- PHASE: C2 (Table3 isoBW calibration)
- CMD: /home/domin/CoherenceTest/APNET/results/hotos_20260306/run_phase2_table3_isobw_cal.sh
- STDOUT: /home/domin/CoherenceTest/APNET/results/hotos_20260306/phase2_table3_isobw_cal/calibration.log and raw/*.log
- FINDING: completed per-placement WB8/WC{8..12} calibration
- GATE_STATUS: PASS

LEDGER_EOF
