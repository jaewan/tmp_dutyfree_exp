#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/20260308_090953/midband_n10}"
RAW="$OUTDIR/raw"
LOG="$OUTDIR/run_midband_chase_n10.log"
LEDGER="$OUTDIR/results_ledger.md"
CSV="$OUTDIR/midband_runs.csv"
SUMMARY="$OUTDIR/midband_summary.csv"

WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-10}"
SEED="${SEED:-20260308}"
VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
WB_THREADS="${WB_THREADS:-2}"
WC_THREADS="${WC_THREADS:-9}"
CORES_CSV="${CORES_CSV:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

mkdir -p "$RAW"
: > "$LOG"
: > "$LEDGER"
printf "run,scenario,mode,threads,bw_gbps,cycles,iters,cpi\n" > "$CSV"
exec > >(tee -a "$LOG") 2>&1

need_exec() {
  [ -x "$1" ] || { echo "ERROR: missing executable $1"; exit 1; }
}

corelist_for_n() {
  local n="$1"
  echo "$CORES_CSV" | tr ',' '\n' | head -"$n" | paste -sd, -
}

metric_bw() {
  local f="$1"
  grep '^RESULT ' "$f" | tail -n1 | sed -E 's/.*bw_gbps=([0-9.]+).*/\1/'
}

metric_cyc_iters() {
  local f="$1"
  awk '
    /^VICTIM /{
      cyc=""; it=""
      for(i=1;i<=NF;i++){
        if($i ~ /^cycles=/){split($i,a,"="); cyc=a[2]}
        if($i ~ /^iters=/){split($i,b,"="); it=b[2]}
      }
      if(cyc!="" && it!=""){print cyc "," it}
    }' "$f" | tail -n1
}

phase_log() {
  local phase="$1" cmd="$2" stdout="$3" finding="$4" gate="$5"
  {
    echo "- PHASE: $phase"
    echo "- CMD: $cmd"
    echo "- STDOUT: $stdout"
    echo "- FINDING: $finding"
    echo "- GATE_STATUS: $gate"
    echo
  } >> "$LEDGER"
}

need_exec "$ROOT/bin/victim"
need_exec "$ROOT/bin/aggressor"

WB_CORES="$(corelist_for_n "$WB_THREADS")"
WC_CORES="$(corelist_for_n "$WC_THREADS")"

echo "=== MIDBAND_N10 START $(date -Is) ==="
echo "config warmup=$WARMUP measure=$MEASURE nruns=$NRUNS seed=$SEED victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE"
echo "config wb_threads=$WB_THREADS wb_cores=$WB_CORES wc_threads=$WC_THREADS wc_cores=$WC_CORES"

for run in $(seq 1 "$NRUNS"); do
  echo "=== RUN_START run=$run ==="

  base_log="$RAW/run${run}_baseline.log"
  "$ROOT/bin/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 4096 -d "$MEASURE" -W "$WARMUP" -P > "$base_log" 2>&1
  base_ci="$(metric_cyc_iters "$base_log")"
  base_cyc="${base_ci%,*}"
  base_it="${base_ci#*,}"
  base_cpi="$(awk -v c="$base_cyc" -v i="$base_it" 'BEGIN{printf "%.6f", c/i}')"
  printf "%s,baseline,none,0,0,%s,%s,%s\n" "$run" "$base_cyc" "$base_it" "$base_cpi" >> "$CSV"

  order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 17041)
a = ["wb","wc"]
r.shuffle(a)
print(" ".join(a))
PY
)"

  for sc in $order; do
    if [ "$sc" = "wb" ]; then
      mode="wb_load"; th="$WB_THREADS"; cores="$WB_CORES"
    else
      mode="wc_ntdqa"; th="$WC_THREADS"; cores="$WC_CORES"
    fi

    vf="$RAW/run${run}_${sc}_victim.log"
    af="$RAW/run${run}_${sc}_aggr.log"

    "$ROOT/bin/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 4096 -d "$MEASURE" -W "$WARMUP" -P > "$vf" 2>&1 &
    vpid=$!
    sleep "$WARMUP"
    "$ROOT/bin/aggressor" -m "$mode" -t "$th" -c "$cores" -s 256 -d "$MEASURE" > "$af" 2>&1
    wait "$vpid"

    bw="$(metric_bw "$af")"
    ci="$(metric_cyc_iters "$vf")"
    cyc="${ci%,*}"
    it="${ci#*,}"
    cpi="$(awk -v c="$cyc" -v i="$it" 'BEGIN{printf "%.6f", c/i}')"
    printf "%s,%s,%s,%s,%s,%s,%s,%s\n" "$run" "$sc" "$mode" "$th" "$bw" "$cyc" "$it" "$cpi" >> "$CSV"
  done

  phase_log "MIDBAND_CHASE_N10" "run=$run baseline+paired(wb,wc)" "$LOG" "completed randomized paired run" "PASS"
  echo "=== RUN_END run=$run ==="
  sleep 2
done

python3 - <<'PY' "$CSV" "$SUMMARY"
import csv, statistics, sys
from scipy import stats

rows = list(csv.DictReader(open(sys.argv[1])))
summary = sys.argv[2]

base = [float(r['cpi']) for r in rows if r['scenario'] == 'baseline']
wb = [float(r['cpi']) for r in rows if r['scenario'] == 'wb']
wc = [float(r['cpi']) for r in rows if r['scenario'] == 'wc']

base_by_run = {int(r['run']): float(r['cpi']) for r in rows if r['scenario'] == 'baseline'}
wb_by_run = {int(r['run']): float(r['cpi']) for r in rows if r['scenario'] == 'wb'}
wc_by_run = {int(r['run']): float(r['cpi']) for r in rows if r['scenario'] == 'wc'}
common = sorted(set(base_by_run) & set(wb_by_run) & set(wc_by_run))

delta_wb = [100.0 * (wb_by_run[r] - base_by_run[r]) / base_by_run[r] for r in common]
delta_wc = [100.0 * (wc_by_run[r] - base_by_run[r]) / base_by_run[r] for r in common]
delta_pair = [delta_wb[i] - delta_wc[i] for i in range(len(common))]
bw_wb = [float(r['bw_gbps']) for r in rows if r['scenario'] == 'wb']
bw_wc = [float(r['bw_gbps']) for r in rows if r['scenario'] == 'wc']

def ms(v):
    return statistics.mean(v), (statistics.stdev(v) if len(v) > 1 else 0.0), len(v)

def cvpct(v):
    m, s, _ = ms(v)
    return 100.0 * s / m if m else float('nan')

welch = stats.ttest_ind(wb, wc, equal_var=False)
paired = stats.ttest_rel(delta_wb, delta_wc)

with open(summary, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['metric','value'])
    m,s,n = ms(base); w.writerow(['base_cpi_mean',f'{m:.6f}']); w.writerow(['base_cpi_sd',f'{s:.6f}']); w.writerow(['base_n',n]); w.writerow(['base_cpi_cv_pct',f'{cvpct(base):.3f}'])
    m,s,n = ms(wb); w.writerow(['wb_cpi_mean',f'{m:.6f}']); w.writerow(['wb_cpi_sd',f'{s:.6f}']); w.writerow(['wb_n',n]); w.writerow(['wb_cpi_cv_pct',f'{cvpct(wb):.3f}'])
    m,s,n = ms(wc); w.writerow(['wc_cpi_mean',f'{m:.6f}']); w.writerow(['wc_cpi_sd',f'{s:.6f}']); w.writerow(['wc_n',n]); w.writerow(['wc_cpi_cv_pct',f'{cvpct(wc):.3f}'])
    m,s,n = ms(delta_wb); w.writerow(['wb_delta_cpi_pct_mean',f'{m:.6f}']); w.writerow(['wb_delta_cpi_pct_sd',f'{s:.6f}']); w.writerow(['wb_delta_n',n])
    m,s,n = ms(delta_wc); w.writerow(['wc_delta_cpi_pct_mean',f'{m:.6f}']); w.writerow(['wc_delta_cpi_pct_sd',f'{s:.6f}']); w.writerow(['wc_delta_n',n])
    m,s,n = ms(delta_pair); w.writerow(['paired_wb_minus_wc_delta_cpi_pct_mean',f'{m:.6f}']); w.writerow(['paired_wb_minus_wc_delta_cpi_pct_sd',f'{s:.6f}']); w.writerow(['paired_n',n])
    m,s,n = ms(bw_wb); w.writerow(['wb_bw_mean_gbps',f'{m:.6f}']); w.writerow(['wb_bw_sd_gbps',f'{s:.6f}']); w.writerow(['wb_bw_n',n])
    m,s,n = ms(bw_wc); w.writerow(['wc_bw_mean_gbps',f'{m:.6f}']); w.writerow(['wc_bw_sd_gbps',f'{s:.6f}']); w.writerow(['wc_bw_n',n])
    w.writerow(['welch_t_wb_vs_wc_cpi', f'{welch.statistic:.6f}'])
    w.writerow(['welch_p_wb_vs_wc_cpi', f'{welch.pvalue:.6e}'])
    w.writerow(['paired_t_delta_wb_vs_wc', f'{paired.statistic:.6f}'])
    w.writerow(['paired_p_delta_wb_vs_wc', f'{paired.pvalue:.6e}'])

print(f'summary={summary}')
PY

phase_log "MIDBAND_CHASE_N10" "$0" "$LOG" "all runs and statistical summary completed" "PASS"
echo "=== MIDBAND_N10 DONE $(date -Is) ==="
