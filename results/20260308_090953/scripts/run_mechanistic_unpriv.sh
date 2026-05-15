#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/20260308_090953/mechanistic_unpriv}"
RAW="$OUTDIR/raw"
LOG="$OUTDIR/run_mechanistic_unpriv.log"
LEDGER="$OUTDIR/results_ledger.md"
CSV="$OUTDIR/mechanistic_summary.csv"

WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
REPS="${REPS:-5}"
SEED="${SEED:-20260308}"
VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
CORES_CSV="${CORES_CSV:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

WB1_THREADS="1"
WB1_CORES="136"
WB8_THREADS="8"
WC11_THREADS="11"
WB8_CORES="136,137,138,139,140,141,142,143"
WC11_CORES="136,137,138,139,140,141,142,143,224,225,226"
PMU_DF_EVENT="${PMU_DF_EVENT:-amd_df/event=0x07,umask=0x38/}"
PMU_L3_EVENT="${PMU_L3_EVENT:-amd_l3/event=0x04,umask=0xff/}"

mkdir -p "$RAW"
: > "$LOG"
: > "$LEDGER"
printf "phase,run,metric,mode,value,unit,context\n" > "$CSV"
exec > >(tee -a "$LOG") 2>&1

need_exec() {
  [ -x "$1" ] || { echo "ERROR: missing executable $1"; exit 1; }
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
command -v perf >/dev/null 2>&1 || { echo "ERROR: missing perf"; exit 1; }

df_ok=0
l3_ok=0
if perf stat -a -e "$PMU_DF_EVENT" sleep 0.1 >/dev/null 2>&1; then df_ok=1; fi
if perf stat -a -e "$PMU_L3_EVENT" sleep 0.1 >/dev/null 2>&1; then l3_ok=1; fi
echo "pmu_precheck df_ok=$df_ok l3_ok=$l3_ok df_event=$PMU_DF_EVENT l3_event=$PMU_L3_EVENT"
printf "precheck,0,pmu_df_available,na,%s,bool,system\n" "$df_ok" >> "$CSV"
printf "precheck,0,pmu_l3_available,na,%s,bool,system\n" "$l3_ok" >> "$CSV"

if [ "$l3_ok" -ne 1 ]; then
  phase_log "MECH_PRECHECK" "$0 precheck" "$LOG" "amd_l3 event unavailable" "BLOCKED"
  exit 1
fi

for run in $(seq 1 "$REPS"); do
  for mode in wb_load wb_prefetchnta wc_ntdqa; do
    af="$RAW/prefetch_bw_${mode}_r${run}.log"
    "$ROOT/bin/aggressor" -m "$mode" -t "$WB1_THREADS" -c "$WB1_CORES" -s 256 -d "$MEASURE" > "$af" 2>&1
    bw="$(metric_bw "$af")"
    printf "prefetch_bw,%s,bw_gbps,%s,%s,GB/s,1thread\n" "$run" "$mode" "$bw" >> "$CSV"
  done
done
phase_log "PREFETCH_BW" "$0 prefetch_bw" "$LOG" "wb_load/wb_prefetchnta/wc_ntdqa 1-thread throughput complete" "PASS"

for run in $(seq 1 "$REPS"); do
  order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 23017)
a = ["wb_load","wc_ntdqa"]
r.shuffle(a)
print(" ".join(a))
PY
)"

  for mode in $order; do
    if [ "$mode" = "wb_load" ]; then
      th="$WB8_THREADS"; cores="$WB8_CORES"
    else
      th="$WC11_THREADS"; cores="$WC11_CORES"
    fi

    vf="$RAW/pmu_victim_${mode}_r${run}.log"
    af="$RAW/pmu_aggr_${mode}_r${run}.log"
    pf="$RAW/pmu_perf_${mode}_r${run}.txt"

    "$ROOT/bin/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 4096 -d "$MEASURE" -W "$WARMUP" -P > "$vf" 2>&1 &
    vpid=$!
    sleep "$WARMUP"
    "$ROOT/bin/aggressor" -m "$mode" -t "$th" -c "$cores" -s 256 -d "$MEASURE" > "$af" 2>&1 &
    apid=$!

    if [ "$df_ok" -eq 1 ]; then
      perf stat -a -x '|' -e "$PMU_DF_EVENT" -e "$PMU_L3_EVENT" -o "$pf" sleep "$MEASURE"
    else
      perf stat -a -x '|' -e "$PMU_L3_EVENT" -o "$pf" sleep "$MEASURE"
    fi

    wait "$apid"
    wait "$vpid"

    bw="$(metric_bw "$af")"
    ci="$(metric_cyc_iters "$vf")"
    cyc="${ci%,*}"
    it="${ci#*,}"
    cpi="$(awk -v c="$cyc" -v i="$it" 'BEGIN{printf "%.6f", c/i}')"

    printf "pmu_pair,%s,bw_gbps,%s,%s,GB/s,chase\n" "$run" "$mode" "$bw" >> "$CSV"
    printf "pmu_pair,%s,cpi,%s,%s,cycles_per_iter,chase\n" "$run" "$mode" "$cpi" >> "$CSV"

    awk -F'|' -v phase="pmu_pair" -v run="$run" -v mode="$mode" '
      NF>=3 {
        gsub(/^[ \t]+|[ \t]+$/, "", $1)
        gsub(/^[ \t]+|[ \t]+$/, "", $3)
        if ($3 != "" && $1 != "") {
          printf "%s,%s,pmu_count,%s,%s,count,%s\n", phase, run, mode, $1, $3
        }
      }
    ' "$pf" >> "$CSV"
  done
  phase_log "PMU_PAIR" "run=$run randomized wb_load/wc_ntdqa" "$LOG" "completed paired PMU run" "PASS"
  sleep 2
done

python3 - <<'PY' "$CSV" "$OUTDIR/pmu_summary.csv"
import csv, statistics, sys
from collections import defaultdict

rows = list(csv.DictReader(open(sys.argv[1])))
out = sys.argv[2]

g = defaultdict(list)
for r in rows:
    key = (r['phase'], r['metric'], r['mode'], r['unit'], r['context'])
    try:
        g[key].append(float(r['value']))
    except ValueError:
        pass

with open(out, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['phase','metric','mode','unit','context','mean','sd','n'])
    for key in sorted(g.keys()):
        vals = g[key]
        m = statistics.mean(vals)
        s = statistics.stdev(vals) if len(vals) > 1 else 0.0
        w.writerow([*key, f'{m:.6f}', f'{s:.6f}', len(vals)])

print(f'summary={out}')
PY

phase_log "MECHANISTIC_UNPRIV" "$0" "$LOG" "all unprivileged phases completed" "PASS"
echo "DONE mechanistic_unpriv"
