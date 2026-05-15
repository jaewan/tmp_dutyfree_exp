#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/hotos_20260306/phase2_table3}"
LOG="$OUTDIR/phase2_table3.log"
LEDGER="$ROOT/results/hotos_20260306/results_ledger.md"

WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-5}"
SEED="${SEED:-20260306}"
AGG_THREADS="${AGG_THREADS:-8}"

# Placement definitions:
# id,victim_core,victim_node,aggressor_cores_csv,notes
PLACEMENTS=(
  "A_same_ccd,136,1,137,138,139,140,141,142,143,399,same CCD; includes one SMT sibling to reach 8 aggressors"
  "B_diff_ccd_same_socket,128,1,136,137,138,139,140,141,142,143,different CCD same socket"
  "C_diff_socket,0,0,136,137,138,139,140,141,142,143,victim socket0 aggressor socket1"
  "D_both_sockets,0,0,16,17,18,19,136,137,138,139,mixed socket0/socket1 aggressors"
)

mkdir -p "$OUTDIR"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

victim_cmd() {
  local profile="$1" core="$2" node="$3"
  if [[ "$profile" == "l2hot" ]]; then
    echo "$ROOT/bin/victim -c $core -n $node -w 64 -d $MEASURE -W $WARMUP"
  elif [[ "$profile" == "chase" ]]; then
    echo "$ROOT/bin/victim -c $core -n $node -w 4096 -d $MEASURE -W $WARMUP -P"
  else
    echo "unknown profile=$profile" >&2
    return 1
  fi
}

run_baseline() {
  local profile="$1" place="$2" run="$3" vcore="$4" vnode="$5"
  echo "=== BASELINE profile=$profile placement=$place run=$run vcore=$vcore vnode=$vnode START ==="
  eval "$(victim_cmd "$profile" "$vcore" "$vnode")"
  local rc=$?
  echo "=== BASELINE_RC profile=$profile placement=$place run=$run rc=$rc ==="
  [[ $rc -eq 0 ]]
}

run_corun() {
  local profile="$1" place="$2" run="$3" vcore="$4" vnode="$5" mode="$6" cores_csv="$7"
  echo "=== CORUN profile=$profile placement=$place run=$run mode=$mode threads=$AGG_THREADS vcore=$vcore vnode=$vnode cores=$cores_csv START ==="
  eval "$(victim_cmd "$profile" "$vcore" "$vnode")" &
  local vpid=$!
  sleep "$WARMUP"
  "$ROOT/bin/aggressor" -m "$mode" -t "$AGG_THREADS" -c "$cores_csv" -s 256 -d "$MEASURE" &
  local apid=$!
  wait "$vpid"; local vrc=$?
  wait "$apid"; local arc=$?
  echo "=== CORUN_RC profile=$profile placement=$place run=$run mode=$mode victim_rc=$vrc aggressor_rc=$arc ==="
  [[ $vrc -eq 0 && $arc -eq 0 ]]
}

echo "=== PHASE2_TABLE3 START ==="
echo "config warmup=$WARMUP measure=$MEASURE nruns=$NRUNS seed=$SEED agg_threads=$AGG_THREADS"
for p in "${PLACEMENTS[@]}"; do
  echo "placement=$p"
done

for profile in l2hot chase; do
  for place_rec in "${PLACEMENTS[@]}"; do
    IFS=',' read -r place_id vcore vnode c1 c2 c3 c4 c5 c6 c7 c8 notes <<< "$place_rec"
    cores_csv="$c1,$c2,$c3,$c4,$c5,$c6,$c7,$c8"
    for run in $(seq 1 "$NRUNS"); do
      echo "=== BLOCK_START profile=$profile placement=$place_id run=$run notes=$notes ==="
      run_baseline "$profile" "$place_id" "$run" "$vcore" "$vnode"
      sleep 2
      order=$(PROFILE="$profile" PLACE="$place_id" RUN="$run" SEED="$SEED" python3 - <<'PY'
import os, random
seed = int(os.environ['SEED'])
r = random.Random(seed + int(os.environ['RUN'])*1297 + hash((os.environ['PROFILE'], os.environ['PLACE']))%100000)
items=['wb_load','wc_ntdqa']
r.shuffle(items)
print(' '.join(items))
PY
)
      echo "--- mode_order=$order ---"
      for mode in $order; do
        run_corun "$profile" "$place_id" "$run" "$vcore" "$vnode" "$mode" "$cores_csv"
        sleep 2
      done
      echo "=== BLOCK_END profile=$profile placement=$place_id run=$run ==="
      {
        echo "- PHASE: C (Phase2 Table3)"
        echo "- CMD: run_block profile=$profile placement=$place_id run=$run via $0"
        echo "- STDOUT: $LOG"
        echo "- FINDING: baseline+wb+wc completed"
        echo "- GATE_STATUS: PASS"
        echo
      } >> "$LEDGER"
    done
  done
done

echo "=== PHASE2_TABLE3 DONE ==="
