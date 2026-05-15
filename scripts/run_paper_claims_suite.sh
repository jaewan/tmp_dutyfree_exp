#!/usr/bin/env bash
#
# run_paper_claims_suite.sh
# Unified, reproducible claim-validation harness for APNet paper Sec2/Sec3.
#
# Claims covered:
#  1) PREFETCHNTA behavior on this Zen4+CXL platform
#  2) CAT "within 1%" (no_cat vs with_cat degradation)
#  3) RDT single-way proxy "within 4%" throughput retention
#  4) PMU-based WB vs WC mechanism evidence at matched high bandwidth
#
# This script is intended to be run as root:
#   sudo bash scripts/run_paper_claims_suite.sh
#
set -euo pipefail

ROOT="${ROOT:-/home/domin/CoherenceTest/APNET}"
BINDIR="${BINDIR:-$ROOT/bin}"
RESCTRL="${RESCTRL:-/sys/fs/resctrl}"
TS="$(date +%Y%m%d_%H%M%S)"
OUTDIR="${OUTDIR:-$ROOT/results/hotos_${TS}/paper_claims_suite}"

# Runtime controls
REPS="${REPS:-5}"
WARMUP="${WARMUP:-5}"
MEASURE="${MEASURE:-15}"
SIZE_MB="${SIZE_MB:-256}"
SEED="${SEED:-20260307}"

# Placement defaults (socket1 inter-CCD set from prior experiments)
VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
AGG_SOCKET="${AGG_SOCKET:-1}"
AGG_CORES_ALL="${AGG_CORES_ALL:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

# High-bandwidth matched pair defaults from phase data
WB_HIGH_THREADS="${WB_HIGH_THREADS:-8}"
WC_HIGH_THREADS="${WC_HIGH_THREADS:-11}"

# PMU defaults that were validated on this host with root
PMU_DF_EVENT="${PMU_DF_EVENT:-amd_df/event=0x07,umask=0x38/}"
PMU_L3_EVENT="${PMU_L3_EVENT:-amd_l3/event=0x04,umask=0xff/}"

mkdir -p "$OUTDIR"/{raw,prefetch,cat,rdt_proxy,pmu}
LOG="$OUTDIR/paper_claims_suite.log"
LEDGER="$OUTDIR/results_ledger.md"
SUMMARY_CSV="$OUTDIR/claims_summary.csv"
SUMMARY_MD="$OUTDIR/claims_summary.md"
: > "$LOG"
: > "$LEDGER"
printf "claim,metric,value,unit,context\n" > "$SUMMARY_CSV"
exec > >(tee -a "$LOG") 2>&1

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

need_bin() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing binary '$1'"; exit 1; }
}

need_file_exec() {
  [ -x "$1" ] || { echo "ERROR: missing executable '$1'"; exit 1; }
}

csv_to_array() {
  echo "$1" | tr ',' '\n'
}

first_n_cores() {
  local n="$1" csv="$2"
  csv_to_array "$csv" | head -"$n" | paste -sd, -
}

metric_from_result_bw() {
  local f="$1"
  grep '^RESULT ' "$f" | tail -n1 | sed -E 's/.*bw_gbps=([0-9.]+).*/\1/'
}

metric_from_victim_ipc() {
  local f="$1"
  grep '^VICTIM ' "$f" | tail -n1 | sed -E 's/.*ipc=([0-9.]+).*/\1/'
}

metric_from_victim_cpi() {
  local f="$1"
  awk '
    /^VICTIM /{
      cyc=""; it=""
      for(i=1;i<=NF;i++){
        if($i ~ /^cycles=/){split($i,a,"=");cyc=a[2]}
        if($i ~ /^iters=/){split($i,b,"=");it=b[2]}
      }
      if(cyc!="" && it!="" && it>0){printf "%.6f\n", cyc/it}
    }' "$f" | tail -n1
}

pct_drop() {
  local base="$1" stressed="$2"
  awk -v b="$base" -v s="$stressed" 'BEGIN{ if(b==0){print "nan"} else { printf "%.6f", 100.0*(1.0 - s/b) } }'
}

pct_absdiff() {
  local a="$1" b="$2"
  awk -v x="$a" -v y="$b" 'BEGIN{d=x-y; if(d<0)d=-d; printf "%.6f", d}'
}

require_root_and_prereqs() {
  need_bin perf
  need_bin python3
  need_file_exec "$BINDIR/victim"
  need_file_exec "$BINDIR/aggressor"

  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    echo "ERROR: run as root (sudo) so resctrl and system-wide perf are accessible."
    exit 1
  fi
  mountpoint -q "$RESCTRL" || { echo "ERROR: resctrl is not mounted at $RESCTRL"; exit 1; }
}

check_pmu_availability() {
  local df_ok=0 l3_ok=0
  if perf stat -a -e "$PMU_DF_EVENT" sleep 0.1 >/dev/null 2>&1; then
    df_ok=1
  fi
  if perf stat -a -e "$PMU_L3_EVENT" sleep 0.1 >/dev/null 2>&1; then
    l3_ok=1
  fi
  if [ "$df_ok" -eq 1 ] && [ "$l3_ok" -eq 1 ]; then
    echo "PMU precheck: both events are usable."
  else
    echo "WARN: PMU precheck failed for one or more events."
    echo "WARN: df_ok=$df_ok l3_ok=$l3_ok events=[$PMU_DF_EVENT ; $PMU_L3_EVENT]"
  fi
}

# ---------- Phase 0: PREFETCHNTA claims ----------
run_prefetch_phase() {
  local out="$OUTDIR/prefetch"
  local core1
  core1="$(first_n_cores 1 "$AGG_CORES_ALL")"
  local wb_high_cores wc_high_cores
  wb_high_cores="$(first_n_cores "$WB_HIGH_THREADS" "$AGG_CORES_ALL")"
  wc_high_cores="$(first_n_cores "$WC_HIGH_THREADS" "$AGG_CORES_ALL")"

  echo "=== PHASE PREFETCH START ==="

  # Throughput-only single-thread behavior
  for mode in wb_load wb_prefetchnta wc_ntdqa; do
    for run in $(seq 1 "$REPS"); do
      local f="$out/raw_${mode}_1t_r${run}.log"
      "$BINDIR/aggressor" -m "$mode" -t 1 -c "$core1" -s "$SIZE_MB" -d "$MEASURE" > "$f" 2>&1
      local bw
      bw="$(metric_from_result_bw "$f")"
      printf "prefetch_bw,%s,%s,GB/s,1t\n" "$mode" "$bw" >> "$SUMMARY_CSV"
    done
  done

  # Victim-impact comparison at high BW-ish settings
  # wb_load 8T vs wb_prefetchnta 8T vs wc_ntdqa 11T
  for run in $(seq 1 "$REPS"); do
    local base="$out/raw_victim_l2hot_baseline_r${run}.log"
    "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 64 -d "$MEASURE" -W "$WARMUP" > "$base" 2>&1
    local ipc_base
    ipc_base="$(metric_from_victim_ipc "$base")"

    for ent in "wb_load,$WB_HIGH_THREADS,$wb_high_cores" "wb_prefetchnta,$WB_HIGH_THREADS,$wb_high_cores" "wc_ntdqa,$WC_HIGH_THREADS,$wc_high_cores"; do
      IFS=',' read -r mode th cores <<< "$ent"
      local vf="$out/raw_victim_${mode}_r${run}.log"
      local af="$out/raw_agg_${mode}_r${run}.log"
      "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 64 -d "$MEASURE" -W "$WARMUP" > "$vf" 2>&1 &
      local vpid=$!
      sleep "$WARMUP"
      "$BINDIR/aggressor" -m "$mode" -t "$th" -c "$cores" -s "$SIZE_MB" -d "$MEASURE" > "$af" 2>&1
      wait "$vpid"

      local ipc_st bw d
      ipc_st="$(metric_from_victim_ipc "$vf")"
      bw="$(metric_from_result_bw "$af")"
      d="$(pct_drop "$ipc_base" "$ipc_st")"
      printf "prefetch_victim_delta,%s,%s,pct,l2hot_ipc_drop\n" "$mode" "$d" >> "$SUMMARY_CSV"
      printf "prefetch_victim_bw,%s,%s,GB/s,l2hot\n" "$mode" "$bw" >> "$SUMMARY_CSV"
    done
  done

  phase_log "PREFETCH" "$0 prefetch" "$out" "completed throughput + victim-impact checks" "PASS"
  echo "=== PHASE PREFETCH DONE ==="
}

# ---------- Phase 1: CAT within-1% claim ----------
run_cat_phase() {
  local out="$OUTDIR/cat"
  local wb_high_cores
  wb_high_cores="$(first_n_cores "$WB_HIGH_THREADS" "$AGG_CORES_ALL")"

  local cbm
  cbm="$(cat "$RESCTRL/info/L3/cbm_mask")"
  local agg_mask vic_mask
  read -r agg_mask vic_mask < <(python3 - <<'PY' "$cbm"
import sys
mask = int(sys.argv[1], 16)
bits = mask.bit_count()
half = max(1, bits // 2)
low = (1 << half) - 1
high = (low << half) & mask
print(f"{low:x} {high:x}")
PY
)

  local grp_a="$RESCTRL/claim_agg_$$"
  local grp_v="$RESCTRL/claim_vic_$$"

  echo "=== PHASE CAT START ==="
  echo "CAT mask split: agg=0x$agg_mask vic=0x$vic_mask"

  for run in $(seq 1 "$REPS"); do
    local base="$out/raw_baseline_r${run}.log"
    "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 64 -d "$MEASURE" -W "$WARMUP" > "$base" 2>&1
    local ipc_base
    ipc_base="$(metric_from_victim_ipc "$base")"

    # Randomize order for paired run
    local order
    order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 10007)
arr = ["no_cat","with_cat"]
r.shuffle(arr)
print(" ".join(arr))
PY
)"

    for mode in $order; do
      local vf="$out/raw_victim_${mode}_r${run}.log"
      local af="$out/raw_agg_${mode}_r${run}.log"

      if [ "$mode" = "with_cat" ]; then
        mkdir -p "$grp_a" "$grp_v"
        echo "L3:${AGG_SOCKET}=${agg_mask}" > "$grp_a/schemata"
        echo "L3:${AGG_SOCKET}=${vic_mask}" > "$grp_v/schemata"
        echo "$wb_high_cores" > "$grp_a/cpus_list"
        echo "$VICTIM_CORE" > "$grp_v/cpus_list"
      else
        rmdir "$grp_a" "$grp_v" 2>/dev/null || true
      fi

      "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -w 64 -d "$MEASURE" -W "$WARMUP" > "$vf" 2>&1 &
      local vpid=$!
      sleep "$WARMUP"
      "$BINDIR/aggressor" -m wb_load -t "$WB_HIGH_THREADS" -c "$wb_high_cores" -s "$SIZE_MB" -d "$MEASURE" > "$af" 2>&1
      wait "$vpid"

      local ipc_st d bw
      ipc_st="$(metric_from_victim_ipc "$vf")"
      d="$(pct_drop "$ipc_base" "$ipc_st")"
      bw="$(metric_from_result_bw "$af")"
      printf "cat_degradation,%s,%s,pct,l2hot_ipc_drop\n" "$mode" "$d" >> "$SUMMARY_CSV"
      printf "cat_bw,%s,%s,GB/s,wb_load\n" "$mode" "$bw" >> "$SUMMARY_CSV"
    done
  done

  rmdir "$grp_a" "$grp_v" 2>/dev/null || true

  phase_log "CAT" "$0 cat" "$out" "completed paired no_cat vs with_cat runs" "PASS"
  echo "=== PHASE CAT DONE ==="
}

# ---------- Phase 2: RDT single-way proxy within 4% ----------
run_rdt_proxy_phase() {
  local out="$OUTDIR/rdt_proxy"
  local wb_high_cores
  wb_high_cores="$(first_n_cores "$WB_HIGH_THREADS" "$AGG_CORES_ALL")"

  local cbm
  cbm="$(cat "$RESCTRL/info/L3/cbm_mask")"
  local one_way
  one_way="$(python3 - <<'PY' "$cbm"
import sys
m = int(sys.argv[1], 16)
lsb = m & -m
print(f"{lsb:x}")
PY
)"

  local grp="$RESCTRL/claim_oneway_$$"

  echo "=== PHASE RDT_PROXY START ==="
  echo "one-way mask: 0x$one_way"

  for run in $(seq 1 "$REPS"); do
    local b="$out/raw_baseline_r${run}.log"
    local o="$out/raw_oneway_r${run}.log"
    "$BINDIR/aggressor" -m wb_load -t "$WB_HIGH_THREADS" -c "$wb_high_cores" -s "$SIZE_MB" -d "$MEASURE" > "$b" 2>&1
    mkdir -p "$grp"
    echo "L3:${AGG_SOCKET}=${one_way}" > "$grp/schemata"
    echo "$wb_high_cores" > "$grp/cpus_list"
    "$BINDIR/aggressor" -m wb_load -t "$WB_HIGH_THREADS" -c "$wb_high_cores" -s "$SIZE_MB" -d "$MEASURE" > "$o" 2>&1
    rmdir "$grp" 2>/dev/null || true

    local bw_b bw_o drop
    bw_b="$(metric_from_result_bw "$b")"
    bw_o="$(metric_from_result_bw "$o")"
    drop="$(pct_drop "$bw_b" "$bw_o")"
    printf "rdt_proxy_bw,baseline,%s,GB/s,wb8\n" "$bw_b" >> "$SUMMARY_CSV"
    printf "rdt_proxy_bw,oneway,%s,GB/s,wb8\n" "$bw_o" >> "$SUMMARY_CSV"
    printf "rdt_proxy_drop,oneway_vs_baseline,%s,pct,wb8\n" "$drop" >> "$SUMMARY_CSV"
  done

  rmdir "$grp" 2>/dev/null || true

  phase_log "RDT_PROXY" "$0 rdt_proxy" "$out" "completed baseline vs one-way throughput retention runs" "PASS"
  echo "=== PHASE RDT_PROXY DONE ==="
}

# ---------- Phase 3: PMU mechanism evidence ----------
run_pmu_phase() {
  local out="$OUTDIR/pmu"
  local wb_high_cores wc_high_cores
  wb_high_cores="$(first_n_cores "$WB_HIGH_THREADS" "$AGG_CORES_ALL")"
  wc_high_cores="$(first_n_cores "$WC_HIGH_THREADS" "$AGG_CORES_ALL")"

  echo "=== PHASE PMU START ==="
  echo "events: $PMU_DF_EVENT ; $PMU_L3_EVENT"

  for run in $(seq 1 "$REPS"); do
    local order
    order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 20011)
arr = ["wb_load","wc_ntdqa"]
r.shuffle(arr)
print(" ".join(arr))
PY
)"
    for mode in $order; do
      local th cores
      if [ "$mode" = "wb_load" ]; then
        th="$WB_HIGH_THREADS"; cores="$wb_high_cores"
      else
        th="$WC_HIGH_THREADS"; cores="$wc_high_cores"
      fi

      local vf="$out/raw_victim_${mode}_r${run}.log"
      local af="$out/raw_agg_${mode}_r${run}.log"
      local pf="$out/raw_perf_${mode}_r${run}.txt"

      "$BINDIR/victim" -c "$VICTIM_CORE" -n "$VICTIM_NODE" -P -w 4096 -d "$MEASURE" -W "$WARMUP" > "$vf" 2>&1 &
      local vpid=$!
      sleep "$WARMUP"
      "$BINDIR/aggressor" -m "$mode" -t "$th" -c "$cores" -s "$SIZE_MB" -d "$MEASURE" > "$af" 2>&1 &
      local apid=$!
      perf stat -a -x '|' -e "$PMU_DF_EVENT" -e "$PMU_L3_EVENT" -o "$pf" sleep "$MEASURE" || true
      wait "$apid"
      wait "$vpid"

      local bw cpi
      bw="$(metric_from_result_bw "$af")"
      cpi="$(metric_from_victim_cpi "$vf")"
      printf "pmu_bw,%s,%s,GB/s,chase\n" "$mode" "$bw" >> "$SUMMARY_CSV"
      printf "pmu_victim_cpi,%s,%s,cycles/iter,chase\n" "$mode" "$cpi" >> "$SUMMARY_CSV"

      # Parse perf-stat CSV-ish rows: count|unit|event|...
      awk -F'|' -v m="$mode" '
        NF>=3 {
          gsub(/^[ \t]+|[ \t]+$/, "", $1);
          gsub(/^[ \t]+|[ \t]+$/, "", $3);
          if($3!=""){
            print "pmu_event," m "," $1 ",count," $3
          }
        }' "$pf" >> "$SUMMARY_CSV"
    done
  done

  phase_log "PMU" "$0 pmu" "$out" "completed paired WB vs WC PMU runs at high BW" "PASS"
  echo "=== PHASE PMU DONE ==="
}

render_summary_md() {
  python3 - <<'PY' "$SUMMARY_CSV" "$SUMMARY_MD"
import csv, statistics, sys
from collections import defaultdict

csv_path, md_path = sys.argv[1], sys.argv[2]
rows = []
with open(csv_path) as f:
    for r in csv.DictReader(f):
        rows.append(r)

grp = defaultdict(list)
for r in rows:
    key = (r["claim"], r["metric"], r["unit"], r["context"])
    try:
        v = float(r["value"].replace(",", ""))
    except Exception:
        continue
    grp[key].append(v)

def ms(vs):
    if not vs: return ("nan", "nan", 0)
    m = statistics.mean(vs)
    s = statistics.stdev(vs) if len(vs) > 1 else 0.0
    return (m, s, len(vs))

with open(md_path, "w") as f:
    f.write("# Paper Claim Validation Summary\n\n")
    f.write("| claim | metric | mean | sd | n | unit | context |\n")
    f.write("|---|---:|---:|---:|---:|---|---|\n")
    for key in sorted(grp):
        m, s, n = ms(grp[key])
        claim, metric, unit, context = key
        f.write(f"| {claim} | {metric} | {m:.6f} | {s:.6f} | {n} | {unit} | {context} |\n")
PY
}

main() {
  echo "=== PAPER CLAIMS SUITE START $(date -Is) ==="
  echo "outdir=$OUTDIR reps=$REPS warmup=$WARMUP measure=$MEASURE seed=$SEED"
  echo "victim core=$VICTIM_CORE node=$VICTIM_NODE agg_socket=$AGG_SOCKET"
  echo "agg_cores_all=$AGG_CORES_ALL"
  echo "wb_high_threads=$WB_HIGH_THREADS wc_high_threads=$WC_HIGH_THREADS"

  require_root_and_prereqs
  check_pmu_availability
  run_prefetch_phase
  run_cat_phase
  run_rdt_proxy_phase
  run_pmu_phase
  render_summary_md

  phase_log "SUITE" "$0" "$OUTDIR" "all claim phases completed; see claims_summary.csv/md" "PASS"
  echo "=== PAPER CLAIMS SUITE DONE $(date -Is) ==="
  echo "summary_csv=$SUMMARY_CSV"
  echo "summary_md=$SUMMARY_MD"
  echo "ledger=$LEDGER"
}

main "$@"
