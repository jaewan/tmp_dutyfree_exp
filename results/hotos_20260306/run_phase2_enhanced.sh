#!/usr/bin/env bash
set -euo pipefail

VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-5}"
SEED="${SEED:-20260306}"
CORES="${CORES:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"
OUTDIR="${OUTDIR:-results/hotos_20260306/enhanced_phase2}"

# Iso-bandwidth pairs from measured Phase-1 curve.
# pair_id, wb_threads, wc_threads
PAIRS="${PAIRS:-low,1,4 high,12,12}"

mkdir -p "$OUTDIR"
LOG="$OUTDIR/phase2_enhanced.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "=== ENHANCED_PHASE2 START ==="
echo "config victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE warmup=$WARMUP measure=$MEASURE runs=$NRUNS seed=$SEED"
echo "config cores=$CORES"
echo "config pairs=$PAIRS"

corelist_for_n() {
  local n="$1"
  echo "$CORES" | tr ',' '\n' | head -"$n" | tr '\n' ',' | sed 's/,$//'
}

victim_cmd() {
  local profile="$1"
  if [ "$profile" = "l2hot" ]; then
    echo "./bin/victim -c $VICTIM_CORE -n $VICTIM_NODE -w 64 -d $MEASURE -W $WARMUP"
  elif [ "$profile" = "chase_miss" ]; then
    echo "./bin/victim -c $VICTIM_CORE -n $VICTIM_NODE -w 4096 -d $MEASURE -W $WARMUP -P"
  else
    echo "unknown profile=$profile" >&2
    return 1
  fi
}

run_baseline() {
  local profile="$1"
  local pair_id="$2"
  local run="$3"
  local tag="$4"
  echo "=== BASELINE profile=$profile pair=$pair_id run=$run tag=$tag ==="
  eval "$(victim_cmd "$profile")"
  echo "=== BASELINE_RC profile=$profile pair=$pair_id run=$run tag=$tag rc=0 ==="
}

run_corun() {
  local profile="$1"
  local pair_id="$2"
  local run="$3"
  local mode="$4"
  local nth="$5"
  local corelist
  corelist="$(corelist_for_n "$nth")"
  echo "=== CORUN profile=$profile pair=$pair_id run=$run mode=$mode threads=$nth START ==="

  eval "$(victim_cmd "$profile")" &
  local vpid=$!
  sleep "$WARMUP"
  ./bin/aggressor -m "$mode" -t "$nth" -c "$corelist" -s 256 -d "$MEASURE" &
  local apid=$!

  wait "$vpid"; local vrc=$?
  wait "$apid"; local arc=$?
  echo "=== CORUN_RC profile=$profile pair=$pair_id run=$run mode=$mode threads=$nth victim=$vrc aggressor=$arc ==="
  [ "$vrc" -eq 0 ] && [ "$arc" -eq 0 ]
}

for profile in l2hot chase_miss; do
  for run in $(seq 1 "$NRUNS"); do
    echo "=== BLOCK_START profile=$profile run=$run ==="
    for p in $PAIRS; do
      IFS=',' read -r pair_id wb_t wc_t <<< "$p"
      order=$(PAIR="$pair_id" RUN="$run" PROFILE="$profile" SEED="$SEED" python3 - <<'PY'
import os, random
seed = int(os.environ["SEED"])
run = int(os.environ["RUN"])
pair = os.environ["PAIR"]
profile = os.environ["PROFILE"]
items = ["wb", "wc"]
r = random.Random(seed + run * 997 + hash((pair, profile)) % 100000)
r.shuffle(items)
print(" ".join(items))
PY
)
      echo "--- pair=$pair_id wb_t=$wb_t wc_t=$wc_t order=$order ---"
      run_baseline "$profile" "$pair_id" "$run" pre
      sleep 2
      for side in $order; do
        if [ "$side" = "wb" ]; then
          run_corun "$profile" "$pair_id" "$run" wb_load "$wb_t"
        else
          run_corun "$profile" "$pair_id" "$run" wc_ntdqa "$wc_t"
        fi
        sleep 2
      done
      run_baseline "$profile" "$pair_id" "$run" post
      sleep 3
    done
    echo "=== BLOCK_END profile=$profile run=$run ==="
  done
done

echo "=== ENHANCED_PHASE2 DONE ==="
