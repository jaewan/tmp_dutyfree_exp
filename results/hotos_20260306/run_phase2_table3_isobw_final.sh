#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/hotos_20260306/phase2_table3_isobw_final}"
LOG="$OUTDIR/phase2_table3_isobw_final.log"
LEDGER="$ROOT/results/hotos_20260306/results_ledger.md"
WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-5}"
SEED="${SEED:-20260306}"
mkdir -p "$OUTDIR"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

# placement_id|victim_core|victim_node|wb_threads|wb_cores|wc_threads|wc_cores|note
PLACEMENTS=(
  "A_same_ccd|136|1|1|137|12|137,138,139,140,141,142,143,393,394,395,396,397|best-feasible: not fully iso-BW on this host"
  "B_diff_ccd_same_socket|128|1|8|136,137,138,139,140,141,142,143|11|136,137,138,139,140,141,142,143,224,225,226|iso-BW"
  "C_diff_socket|0|0|8|136,137,138,139,140,141,142,143|11|136,137,138,139,140,141,142,143,224,225,226|iso-BW"
  "D_both_sockets|0|0|8|16,17,18,19,136,137,138,139|12|16,17,18,19,136,137,138,139,272,273,274,275|near-iso-BW"
)

victim_cmd(){
  local profile="$1" core="$2" node="$3"
  if [[ "$profile" == "l2hot" ]]; then
    echo "$ROOT/bin/victim -c $core -n $node -w 64 -d $MEASURE -W $WARMUP"
  else
    echo "$ROOT/bin/victim -c $core -n $node -w 4096 -d $MEASURE -W $WARMUP -P"
  fi
}

run_baseline(){
  local profile="$1" place="$2" run="$3" vcore="$4" vnode="$5"
  echo "=== BASELINE profile=$profile placement=$place run=$run vcore=$vcore vnode=$vnode START ==="
  eval "$(victim_cmd "$profile" "$vcore" "$vnode")"
  local rc=$?
  echo "=== BASELINE_RC profile=$profile placement=$place run=$run rc=$rc ==="
  [[ $rc -eq 0 ]]
}

run_corun(){
  local profile="$1" place="$2" run="$3" vcore="$4" vnode="$5" mode="$6" th="$7" cores="$8"
  echo "=== CORUN profile=$profile placement=$place run=$run mode=$mode threads=$th vcore=$vcore vnode=$vnode cores=$cores START ==="
  eval "$(victim_cmd "$profile" "$vcore" "$vnode")" &
  local vpid=$!
  sleep "$WARMUP"
  "$ROOT/bin/aggressor" -m "$mode" -t "$th" -c "$cores" -s 256 -d "$MEASURE" &
  local apid=$!
  wait "$vpid"; local vrc=$?
  wait "$apid"; local arc=$?
  echo "=== CORUN_RC profile=$profile placement=$place run=$run mode=$mode victim_rc=$vrc aggressor_rc=$arc ==="
  [[ $vrc -eq 0 && $arc -eq 0 ]]
}

echo "=== PHASE2_TABLE3_ISOBW_FINAL START ==="
echo "config warmup=$WARMUP measure=$MEASURE nruns=$NRUNS seed=$SEED"

for profile in l2hot chase; do
  for rec in "${PLACEMENTS[@]}"; do
    IFS='|' read -r place vcore vnode wb_t wb_cores wc_t wc_cores note <<< "$rec"

    for run in $(seq 1 "$NRUNS"); do
      echo "=== BLOCK_START profile=$profile placement=$place run=$run note=$note ==="
      run_baseline "$profile" "$place" "$run" "$vcore" "$vnode"
      sleep 2
      order=$(PROFILE="$profile" PLACE="$place" RUN="$run" SEED="$SEED" python3 - <<'PY'
import os, random
r=random.Random(int(os.environ['SEED'])+int(os.environ['RUN'])*1999+hash((os.environ['PROFILE'],os.environ['PLACE']))%100000)
m=['wb_load','wc_ntdqa']; r.shuffle(m); print(' '.join(m))
PY
)
      echo "--- wb_t=$wb_t wc_t=$wc_t mode_order=$order ---"
      for mode in $order; do
        if [[ "$mode" == "wb_load" ]]; then
          run_corun "$profile" "$place" "$run" "$vcore" "$vnode" "$mode" "$wb_t" "$wb_cores"
        else
          run_corun "$profile" "$place" "$run" "$vcore" "$vnode" "$mode" "$wc_t" "$wc_cores"
        fi
        sleep 2
      done
      echo "=== BLOCK_END profile=$profile placement=$place run=$run ==="
      {
        echo "- PHASE: C3 (Table3 isoBW final)"
        echo "- CMD: run_block profile=$profile placement=$place run=$run via $0"
        echo "- STDOUT: $LOG"
        echo "- FINDING: baseline+wb+wc completed"
        echo "- GATE_STATUS: PASS"
        echo
      } >> "$LEDGER"
    done
  done
done

echo "=== PHASE2_TABLE3_ISOBW_FINAL DONE ==="
