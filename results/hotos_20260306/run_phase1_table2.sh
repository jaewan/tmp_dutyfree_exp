#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/hotos_20260306/phase1_table2}"
LOG="$OUTDIR/phase1_table2.log"
LEDGER="$ROOT/results/hotos_20260306/results_ledger.md"

VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
NRUNS="${NRUNS:-5}"
SEED="${SEED:-20260306}"
CORES_CSV="${CORES_CSV:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

# Matched pairs from Phase A + diagnostic:
# low:  wb1 ~15.75 vs wc4 ~15.38
# mid:  wb2 ~20.78 vs wc9 ~20.96
# high: wb8 ~24.82 vs wc12 ~24.92
SCENARIOS=(
  "baseline,none,0,none"
  "low_wb,wb_load,1,low"
  "low_wc,wc_ntdqa,4,low"
  "mid_wb,wb_load,2,mid"
  "mid_wc,wc_ntdqa,9,mid"
  "high_wb,wb_load,8,high"
  "high_wc,wc_ntdqa,12,high"
)

mkdir -p "$OUTDIR"
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

corelist_for_n() {
  local n="$1"
  echo "$CORES_CSV" | tr ',' '\n' | head -"$n" | tr '\n' ',' | sed 's/,$//'
}

victim_cmd() {
  local profile="$1"
  if [[ "$profile" == "l2hot" ]]; then
    echo "$ROOT/bin/victim -c $VICTIM_CORE -n $VICTIM_NODE -w 64 -d $MEASURE -W $WARMUP"
  elif [[ "$profile" == "chase" ]]; then
    echo "$ROOT/bin/victim -c $VICTIM_CORE -n $VICTIM_NODE -w 4096 -d $MEASURE -W $WARMUP -P"
  else
    echo "unknown profile=$profile" >&2
    return 1
  fi
}

run_scenario() {
  local profile="$1" run="$2" scenario="$3" mode="$4" threads="$5" band="$6"
  echo "=== SCENARIO profile=$profile run=$run scenario=$scenario mode=$mode threads=$threads band=$band START ==="

  if [[ "$mode" == "none" ]]; then
    eval "$(victim_cmd "$profile")"
    local rc=$?
    echo "=== SCENARIO_RC profile=$profile run=$run scenario=$scenario mode=$mode threads=$threads band=$band victim_rc=$rc aggressor_rc=0 ==="
    [[ $rc -eq 0 ]]
    return
  fi

  local corelist
  corelist="$(corelist_for_n "$threads")"
  eval "$(victim_cmd "$profile")" &
  local vpid=$!
  sleep "$WARMUP"
  "$ROOT/bin/aggressor" -m "$mode" -t "$threads" -c "$corelist" -s 256 -d "$MEASURE" &
  local apid=$!

  wait "$vpid"; local vrc=$?
  wait "$apid"; local arc=$?
  echo "=== SCENARIO_RC profile=$profile run=$run scenario=$scenario mode=$mode threads=$threads band=$band victim_rc=$vrc aggressor_rc=$arc ==="
  [[ $vrc -eq 0 && $arc -eq 0 ]]
}

echo "=== PHASE1_TABLE2 START ==="
echo "config victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE warmup=$WARMUP measure=$MEASURE nruns=$NRUNS seed=$SEED"
echo "config cores=$CORES_CSV"

for profile in l2hot chase; do
  for run in $(seq 1 "$NRUNS"); do
    echo "=== RUN_BLOCK_START profile=$profile run=$run ==="
    mapfile -t order < <(
      SCEN_LIST="$(printf '%s\n' "${SCENARIOS[@]}")" PROFILE="$profile" RUN="$run" SEED="$SEED" \
      python3 - <<'PY'
import os, random
seed = int(os.environ['SEED'])
run = int(os.environ['RUN'])
profile = os.environ['PROFILE']
scens = [ln for ln in os.environ['SCEN_LIST'].splitlines() if ln.strip()]
r = random.Random(seed + run * 1009 + (0 if profile == 'l2hot' else 1) * 8191)
r.shuffle(scens)
for s in scens:
    print(s)
PY
    )

    for ent in "${order[@]}"; do
      IFS=',' read -r scenario mode threads band <<< "$ent"
      run_scenario "$profile" "$run" "$scenario" "$mode" "$threads" "$band"
      sleep 2
    done

    echo "=== RUN_BLOCK_END profile=$profile run=$run ==="
    {
      echo "- PHASE: B (Phase1 Table2)"
      echo "- CMD: run_block profile=$profile run=$run via $0"
      echo "- STDOUT: $LOG"
      echo "- FINDING: completed randomized 7-scenario block"
      echo "- GATE_STATUS: PASS"
      echo
    } >> "$LEDGER"
  done
done

echo "=== PHASE1_TABLE2 DONE ==="
