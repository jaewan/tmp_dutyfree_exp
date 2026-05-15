#!/usr/bin/env bash
set -euo pipefail

VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-10}"
SEED="${SEED:-20260306}"
CORES_WB="${CORES_WB:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"
THREAD_POINTS="${THREAD_POINTS:-1 2 4 6 8 12 16}"
OUTDIR="${OUTDIR:-results/hotos_20260305/phase2}"

if [ -z "${VICTIM_WS_KB:-}" ]; then
  L2B=$(getconf LEVEL2_CACHE_SIZE 2>/dev/null || true)
  if [ -n "$L2B" ] && [ "$L2B" -gt 0 ] 2>/dev/null; then
    VICTIM_WS_KB=$(( (L2B * 3 / 4) / 1024 / 3 ))
  else
    VICTIM_WS_KB=256
  fi
fi

mkdir -p "$OUTDIR"
LOG="$OUTDIR/phase2_corun.log"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "=== PHASE2 START ==="
echo "config victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE ws_kb=$VICTIM_WS_KB warmup=$WARMUP measure=$MEASURE nruns=$NRUNS seed=$SEED"
echo "config cores=$CORES_WB"

scenario_corelist() {
  local nthreads="$1"
  echo "$CORES_WB" | tr ',' '\n' | head -"$nthreads" | tr '\n' ',' | sed 's/,$//'
}

one_trial() {
  local mode="$1"
  local nthreads="$2"
  local run="$3"
  local corelist
  corelist="$(scenario_corelist "$nthreads")"

  echo "=== PHASE2 MODE=$mode T=$nthreads RUN=$run BASELINE ==="
  ./bin/victim -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" -d "$MEASURE" -W "$WARMUP"
  echo "=== PHASE2 MODE=$mode T=$nthreads RUN=$run BASELINE_RC=0 ==="
  sleep 2

  echo "=== PHASE2 MODE=$mode T=$nthreads RUN=$run CORUN ==="
  ./bin/victim -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w "$VICTIM_WS_KB" -d "$MEASURE" -W "$WARMUP" &
  local vpid=$!
  sleep "$WARMUP"
  ./bin/aggressor -m "$mode" -t "$nthreads" -c "$corelist" -s 256 -d "$MEASURE" &
  local apid=$!

  wait "$vpid"
  local v_rc=$?
  wait "$apid"
  local a_rc=$?
  echo "=== PHASE2 MODE=$mode T=$nthreads RUN=$run CORUN_RC victim=$v_rc aggressor=$a_rc ==="
  [ "$v_rc" -eq 0 ] && [ "$a_rc" -eq 0 ]
  sleep 3
}

for run in $(seq 1 "$NRUNS"); do
  echo "=== PHASE2 RUN_INDEX=$run START ==="
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
    one_trial "$mode" "$nthreads" "$run"
  done
  echo "=== PHASE2 RUN_INDEX=$run END ==="
done

echo "=== PHASE2 DONE ==="
