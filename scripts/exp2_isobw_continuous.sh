#!/usr/bin/env bash
# Continuous, no-throttle iso-bandwidth experiment:
# compare WB and WC by thread-count matching under identical timing windows.
set -euo pipefail

BINDIR="${BINDIR:-./bin}"
OUTDIR="${OUTDIR:-./results/exp2_continuous}"
VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
WARMUP="${WARMUP_SEC:-5}"
MEASURE="${MEASURE_SEC:-15}"
NUM_RUNS="${NUM_RUNS:-10}"
SEED="${SEED:-20260306}"
THREAD_POINTS="${THREAD_POINTS:-1 2 4 6 8 12 16}"
CORES_WB="${AGG_CORE_LIST:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

if [ -z "${VICTIM_WS_KB:-}" ]; then
  l2b=$(getconf LEVEL2_CACHE_SIZE 2>/dev/null || true)
  if [ -n "$l2b" ] && [ "$l2b" -gt 0 ] 2>/dev/null; then
    VICTIM_WS_KB=$(( (l2b * 3 / 4) / 1024 / 3 ))
  else
    VICTIM_WS_KB=256
  fi
fi

mkdir -p "$OUTDIR"
LOG="$OUTDIR/phase2_corun.log"
exec > >(tee -a "$LOG") 2>&1

echo "=== EXP2 CONTINUOUS START ==="
echo "config victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE ws_kb=$VICTIM_WS_KB warmup=$WARMUP measure=$MEASURE runs=$NUM_RUNS seed=$SEED"
echo "config cores=$CORES_WB thread_points=$THREAD_POINTS"

scenario_corelist() {
  local nthreads="$1"
  echo "$CORES_WB" | tr ',' '\n' | head -"$nthreads" | tr '\n' ',' | sed 's/,$//'
}

run_once() {
  local mode="$1"
  local nthreads="$2"
  local run="$3"
  local corelist
  corelist="$(scenario_corelist "$nthreads")"

  echo "=== MODE=$mode THREADS=$nthreads RUN=$run BASELINE ==="
  "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" -d "$MEASURE" -W "$WARMUP"
  echo "=== MODE=$mode THREADS=$nthreads RUN=$run BASELINE_RC=0 ==="
  sleep 2

  echo "=== MODE=$mode THREADS=$nthreads RUN=$run CORUN ==="
  "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" -d "$MEASURE" -W "$WARMUP" &
  local vpid=$!
  sleep "$WARMUP"
  "$BINDIR/aggressor" -m "$mode" -t "$nthreads" -c "$corelist" -s 256 -d "$MEASURE" &
  local apid=$!
  wait "$vpid"
  local v_rc=$?
  wait "$apid"
  local a_rc=$?
  echo "=== MODE=$mode THREADS=$nthreads RUN=$run CORUN_RC victim=$v_rc aggressor=$a_rc ==="
  [ "$v_rc" -eq 0 ] && [ "$a_rc" -eq 0 ]
  sleep 3
}

for run in $(seq 1 "$NUM_RUNS"); do
  echo "=== RUN_INDEX=$run START ==="
  order=$(THREAD_POINTS="$THREAD_POINTS" SEED="$SEED" RUN="$run" python3 - <<'PY'
import os, random
threads = [int(x) for x in os.environ["THREAD_POINTS"].split()]
pairs = [(m, t) for m in ("wb_load", "wc_ntdqa") for t in threads]
r = random.Random(int(os.environ["SEED"]) + int(os.environ["RUN"]))
r.shuffle(pairs)
print(" ".join(f"{m}:{t}" for m, t in pairs))
PY
)
  echo "order=$order"
  for item in $order; do
    mode="${item%%:*}"
    nthreads="${item##*:}"
    run_once "$mode" "$nthreads" "$run"
  done
  echo "=== RUN_INDEX=$run END ==="
done

echo "=== EXP2 CONTINUOUS DONE ==="
