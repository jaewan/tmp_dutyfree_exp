#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="$ROOT/results/hotos_20260306/phase0_scaling"
RAWDIR="$OUTDIR/raw"
LEDGER="$ROOT/results/hotos_20260306/results_ledger.md"
CSV="$OUTDIR/phase0_scaling.csv"
SUMMARY="$OUTDIR/phase0_scaling_summary.csv"
ANALYSIS_TXT="$OUTDIR/phase0_scaling_analysis.txt"

mkdir -p "$RAWDIR"

CORES=(136 137 138 139 140 141 142 143 224 225 226 227 228 229 230 231)
THREADS=(1 2 3 4 6 8 10 12 16)
MODES=(wb_load wc_ntdqa)
REPS=3
DUR=15
SIZE_MB=256

printf "mode,threads,run,bw_gbps\n" > "$CSV"

{
  echo "- PHASE: A (Phase0 scaling)"
  echo "- CMD: $(realpath "$0")"
  echo "- STDOUT: $OUTDIR/phase0_scaling.log"
  echo "- FINDING: started"
  echo "- GATE_STATUS: IN_PROGRESS"
  echo
} >> "$LEDGER"

LOG="$OUTDIR/phase0_scaling.log"
: > "$LOG"

echo "[phase0] start $(date -Is)" | tee -a "$LOG"
echo "[phase0] modes=${MODES[*]} threads=${THREADS[*]} reps=$REPS dur=$DUR size_mb=$SIZE_MB" | tee -a "$LOG"
echo "[phase0] cores=${CORES[*]}" | tee -a "$LOG"

for mode in "${MODES[@]}"; do
  for th in "${THREADS[@]}"; do
    corelist=$(IFS=,; echo "${CORES[*]:0:$th}")
    for run in $(seq 1 "$REPS"); do
      outf="$RAWDIR/${mode}_t${th}_r${run}.log"
      cmd=("$ROOT/bin/aggressor" -m "$mode" -t "$th" -c "$corelist" -s "$SIZE_MB" -d "$DUR")
      echo "[phase0] RUN mode=$mode threads=$th run=$run cmd=${cmd[*]}" | tee -a "$LOG"
      set +e
      timeout 45s "${cmd[@]}" > "$outf" 2>&1
      rc=$?
      set -e
      cat "$outf" >> "$LOG"
      if [[ $rc -ne 0 ]]; then
        echo "[phase0] ERROR rc=$rc mode=$mode threads=$th run=$run" | tee -a "$LOG"
        {
          echo "- PHASE: A (Phase0 scaling)"
          echo "- CMD: ${cmd[*]}"
          echo "- STDOUT: $outf"
          echo "- FINDING: command failed rc=$rc"
          echo "- GATE_STATUS: FAIL"
          echo
        } >> "$LEDGER"
        exit 1
      fi
      bw=$(grep '^RESULT ' "$outf" | sed -E 's/.*bw_gbps=([0-9.]+).*/\1/' | tail -n1)
      if [[ -z "$bw" ]]; then
        echo "[phase0] ERROR missing RESULT line mode=$mode threads=$th run=$run" | tee -a "$LOG"
        {
          echo "- PHASE: A (Phase0 scaling)"
          echo "- CMD: ${cmd[*]}"
          echo "- STDOUT: $outf"
          echo "- FINDING: missing RESULT line"
          echo "- GATE_STATUS: FAIL"
          echo
        } >> "$LEDGER"
        exit 1
      fi
      printf "%s,%s,%s,%s\n" "$mode" "$th" "$run" "$bw" >> "$CSV"
      {
        echo "- PHASE: A (Phase0 scaling)"
        echo "- CMD: ${cmd[*]}"
        echo "- STDOUT: $outf"
        echo "- FINDING: bw_gbps=$bw"
        echo "- GATE_STATUS: PASS"
        echo
      } >> "$LEDGER"
      sleep 1
    done
  done
done

python3 - <<'PY' "$CSV" "$SUMMARY" "$ANALYSIS_TXT"
import csv, statistics, sys
from collections import defaultdict
csv_in, summary_out, analysis_out = sys.argv[1:4]
rows = defaultdict(list)
with open(csv_in) as f:
    r = csv.DictReader(f)
    for x in r:
        rows[(x['mode'], int(x['threads']))].append(float(x['bw_gbps']))
with open(summary_out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['mode','threads','mean_bw_gbps','sd_bw_gbps','n'])
    for (mode, th) in sorted(rows.keys(), key=lambda t: (t[0], t[1])):
        vals = rows[(mode,th)]
        mean = statistics.mean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        w.writerow([mode, th, f"{mean:.3f}", f"{sd:.3f}", len(vals)])

def nearest_pair(target, wb, wc):
    best = None
    for twb, bwb in wb.items():
        for twc, bwc in wc.items():
            avg = 0.5*(bwb+bwc)
            score = abs(avg-target) + 0.6*abs(bwb-bwc)
            cand = (score, twb, bwb, twc, bwc, avg, bwb-bwc)
            if best is None or cand < best:
                best = cand
    return best

wb = {th: statistics.mean(rows[('wb_load',th)]) for th in sorted({k[1] for k in rows if k[0]=='wb_load'})}
wc = {th: statistics.mean(rows[('wc_ntdqa',th)]) for th in sorted({k[1] for k in rows if k[0]=='wc_ntdqa'})}

pairs = {
    'low_16': nearest_pair(16.0, wb, wc),
    'mid_21': nearest_pair(21.0, wb, wc),
    'high_25': nearest_pair(25.0, wb, wc),
}

with open(analysis_out, 'w') as f:
    f.write('Phase0 scaling analysis\n')
    f.write('=======================\n')
    f.write('WB means by threads:\n')
    for th in sorted(wb):
        f.write(f'  wb_load t={th}: {wb[th]:.3f} GB/s\n')
    f.write('WC means by threads:\n')
    for th in sorted(wc):
        f.write(f'  wc_ntdqa t={th}: {wc[th]:.3f} GB/s\n')
    f.write('\nMatched pairs:\n')
    for name, b in pairs.items():
        _, twb, bwb, twc, bwc, avg, diff = b
        f.write(f'  {name}: wb t={twb} ({bwb:.3f}) vs wc t={twc} ({bwc:.3f}), avg={avg:.3f}, delta={diff:+.3f}\n')

print('done')
PY

{
  echo "- PHASE: A (Phase0 scaling)"
  echo "- CMD: python3 summarize $CSV"
  echo "- STDOUT: $SUMMARY ; $ANALYSIS_TXT"
  echo "- FINDING: summary generated"
  echo "- GATE_STATUS: PASS"
  echo
} >> "$LEDGER"

echo "[phase0] complete $(date -Is)" | tee -a "$LOG"
