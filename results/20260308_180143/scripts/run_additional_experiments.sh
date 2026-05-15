#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/domin/CoherenceTest/APNET"
OUTDIR="${OUTDIR:-$ROOT/results/20260308_180143}"
RAW="$OUTDIR/raw"
LOG="$OUTDIR/run_additional_experiments.log"
LEDGER="$OUTDIR/results_ledger.md"
RUNS_CSV="$OUTDIR/all_runs.csv"
A_SUMMARY="$OUTDIR/experimentA_summary.csv"
B_SUMMARY="$OUTDIR/experimentB_summary.csv"
PAIR_CSV="$OUTDIR/paired_stats.csv"

# Runtime controls
SEED="${SEED:-20260308}"
WARMUP_A="${WARMUP_A:-5}"
MEASURE_A="${MEASURE_A:-15}"
NRUNS_A="${NRUNS_A:-10}"
WARMUP_B="${WARMUP_B:-5}"
MEASURE_B="${MEASURE_B:-15}"
NRUNS_B="${NRUNS_B:-5}"

VICTIM_CORE="${VICTIM_CORE:-128}"
VICTIM_NODE="${VICTIM_NODE:-1}"
VICTIM_WS_KB="${VICTIM_WS_KB:-4096}"
CORES_ALL="${CORES_ALL:-136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231}"

WB_THREADS_A="${WB_THREADS_A:-2}"
WC_THREADS_A="${WC_THREADS_A:-9}"
COL_THREADS_B="${COL_THREADS_B:-8}"
AGG_MB_PER_THREAD="${AGG_MB_PER_THREAD:-256}"

mkdir -p "$OUTDIR" "$RAW/expA" "$RAW/expB"
: > "$LOG"
: > "$RUNS_CSV"
printf "exp,run,scenario,mode,threads,order_idx,cpi,bw_gbps,log_path,status\n" > "$RUNS_CSV"

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

need_exec() {
  [ -x "$1" ] || { echo "ERROR: missing executable $1"; exit 1; }
}

first_n_cores() {
  local n="$1"
  echo "$CORES_ALL" | tr ',' '\n' | head -"$n" | paste -sd, -
}

metric_bw() {
  local f="$1"
  grep '^RESULT ' "$f" | tail -n1 | sed -E 's/.*bw_gbps=([0-9.]+).*/\1/'
}

metric_cpi() {
  local f="$1"
  awk '
    /^VICTIM /{
      cpi=""
      for(i=1;i<=NF;i++){
        if($i ~ /^cycles_per_iter=/){split($i,a,"="); cpi=a[2]}
      }
      if(cpi!=""){print cpi}
    }' "$f" | tail -n1
}

safe_run() {
  local logf="$1"
  shift
  if "$@" > "$logf" 2>&1; then
    return 0
  fi
  return 1
}

echo "=== ADDITIONAL EXPERIMENTS START $(date -Is) ==="
echo "config seed=$SEED victim_core=$VICTIM_CORE victim_node=$VICTIM_NODE victim_ws_kb=$VICTIM_WS_KB"
echo "config expA warmup=$WARMUP_A measure=$MEASURE_A runs=$NRUNS_A wb_threads=$WB_THREADS_A wc_threads=$WC_THREADS_A"
echo "config expB warmup=$WARMUP_B measure=$MEASURE_B runs=$NRUNS_B col_threads=$COL_THREADS_B"
echo "config cores_all=$CORES_ALL agg_mb_per_thread=$AGG_MB_PER_THREAD"

# Pre-run checks
need_exec "$ROOT/bin/intra_app_corun"
need_exec "$ROOT/bin/aggressor"
need_exec "$ROOT/bin/victim"

if [ ! -c /dev/cxl_wc ]; then
  phase_log "PRECHECK" "test -c /dev/cxl_wc" "$LOG" "/dev/cxl_wc missing" "BLOCKED"
  echo "ERROR: /dev/cxl_wc missing; cannot run WB/WC matched pair experiments."
  exit 1
fi

STALE="$OUTDIR/stale_processes.txt"
ps -eo pid,ppid,cmd --sort=start_time | rg "bin/(victim|aggressor|intra_app_corun)|run_additional_experiments" -S > "$STALE" || true
phase_log "PRECHECK" "ps ... | rg bin/(victim|aggressor|intra_app_corun)" "$STALE" "captured stale-process snapshot" "PASS"

TOPO="$OUTDIR/topology_snapshot.txt"
{
  lscpu -e=cpu,node,socket,core | sed -n '1,80p'
  echo "---"
  numactl --hardware
} > "$TOPO" 2>&1
phase_log "PRECHECK" "lscpu/numactl topology snapshot" "$TOPO" "captured current topology/core mapping" "PASS"

BUILD_LOG="$OUTDIR/build.log"
if make -C "$ROOT" -j4 > "$BUILD_LOG" 2>&1; then
  phase_log "PRECHECK" "make -C $ROOT -j4" "$BUILD_LOG" "build succeeded" "PASS"
else
  phase_log "PRECHECK" "make -C $ROOT -j4" "$BUILD_LOG" "build failed" "FAIL"
  exit 1
fi

WB_CORES_A="$(first_n_cores "$WB_THREADS_A")"
WC_CORES_A="$(first_n_cores "$WC_THREADS_A")"
COL_CORES_B="$(first_n_cores "$COL_THREADS_B")"

# Experiment A
for run in $(seq 1 "$NRUNS_A"); do
  echo "=== EXP_A RUN $run START ==="

  base_log="$RAW/expA/run${run}_baseline.log"
  base_seed=$((SEED + run * 1000 + 1))
  if safe_run "$base_log" "$ROOT/bin/intra_app_corun" \
      -c "$VICTIM_CORE" -n "$VICTIM_NODE" -v "$VICTIM_WS_KB" -W "$WARMUP_A" -d "$MEASURE_A" \
      -S "$base_seed" -m none; then
    base_cpi="$(metric_cpi "$base_log")"
    base_bw="$(metric_bw "$base_log")"
    printf "A,%s,baseline,none,0,0,%s,%s,%s,PASS\n" "$run" "$base_cpi" "$base_bw" "$base_log" >> "$RUNS_CSV"
  else
    printf "A,%s,baseline,none,0,0,NA,NA,%s,FAIL\n" "$run" "$base_log" >> "$RUNS_CSV"
    phase_log "EXP_A" "run=$run baseline" "$base_log" "baseline run failed" "FAIL"
    exit 1
  fi

  order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 17041)
a = ["wb", "wc"]
r.shuffle(a)
print(" ".join(a))
PY
)"

  idx=1
  for sc in $order; do
    if [ "$sc" = "wb" ]; then
      mode="wb_load"; th="$WB_THREADS_A"; cores="$WB_CORES_A"
    else
      mode="wc_ntdqa"; th="$WC_THREADS_A"; cores="$WC_CORES_A"
    fi
    logf="$RAW/expA/run${run}_${sc}.log"
    sc_seed=$((SEED + run * 1000 + idx * 17 + 3))
    if safe_run "$logf" "$ROOT/bin/intra_app_corun" \
        -c "$VICTIM_CORE" -n "$VICTIM_NODE" -v "$VICTIM_WS_KB" -W "$WARMUP_A" -d "$MEASURE_A" \
        -S "$sc_seed" -m "$mode" -t "$th" -a "$cores" -s "$AGG_MB_PER_THREAD"; then
      cpi="$(metric_cpi "$logf")"
      bw="$(metric_bw "$logf")"
      printf "A,%s,%s,%s,%s,%s,%s,%s,%s,PASS\n" "$run" "$sc" "$mode" "$th" "$idx" "$cpi" "$bw" "$logf" >> "$RUNS_CSV"
    else
      printf "A,%s,%s,%s,%s,%s,NA,NA,%s,FAIL\n" "$run" "$sc" "$mode" "$th" "$idx" "$logf" >> "$RUNS_CSV"
      phase_log "EXP_A" "run=$run scenario=$sc mode=$mode" "$logf" "scenario run failed" "FAIL"
      exit 1
    fi
    idx=$((idx + 1))
  done

  phase_log "EXP_A" "run=$run baseline+paired(WB/WC randomized)" "$RAW/expA" "completed run with deterministic order" "PASS"
  echo "=== EXP_A RUN $run END ==="
  sleep 2
done

# Experiment B
for run in $(seq 1 "$NRUNS_B"); do
  echo "=== EXP_B RUN $run START ==="

  order="$(python3 - <<'PY' "$SEED" "$run"
import random, sys
r = random.Random(int(sys.argv[1]) + int(sys.argv[2]) * 29021)
a = ["baseline", "columnar_wb"]
r.shuffle(a)
print(" ".join(a))
PY
)"

  idx=1
  for sc in $order; do
    if [ "$sc" = "baseline" ]; then
      mode="none"; th=0; extra=()
    else
      mode="wb_column_scan"; th="$COL_THREADS_B"; extra=(-t "$COL_THREADS_B" -a "$COL_CORES_B" -s "$AGG_MB_PER_THREAD")
    fi

    logf="$RAW/expB/run${run}_${sc}.log"
    sc_seed=$((SEED + 500000 + run * 1000 + idx * 31))

    if safe_run "$logf" "$ROOT/bin/intra_app_corun" \
        -c "$VICTIM_CORE" -n "$VICTIM_NODE" -v "$VICTIM_WS_KB" -W "$WARMUP_B" -d "$MEASURE_B" \
        -S "$sc_seed" -m "$mode" "${extra[@]}"; then
      cpi="$(metric_cpi "$logf")"
      bw="$(metric_bw "$logf")"
      printf "B,%s,%s,%s,%s,%s,%s,%s,%s,PASS\n" "$run" "$sc" "$mode" "$th" "$idx" "$cpi" "$bw" "$logf" >> "$RUNS_CSV"
    else
      printf "B,%s,%s,%s,%s,%s,NA,NA,%s,FAIL\n" "$run" "$sc" "$mode" "$th" "$idx" "$logf" >> "$RUNS_CSV"
      phase_log "EXP_B" "run=$run scenario=$sc mode=$mode" "$logf" "scenario run failed" "FAIL"
      exit 1
    fi
    idx=$((idx + 1))
  done

  phase_log "EXP_B" "run=$run randomized(baseline,columnar_wb)" "$RAW/expB" "completed run with deterministic order" "PASS"
  echo "=== EXP_B RUN $run END ==="
  sleep 2
done

python3 - <<'PY' "$RUNS_CSV" "$A_SUMMARY" "$B_SUMMARY" "$PAIR_CSV"
import csv, itertools, math, statistics, sys

runs_csv, a_out, b_out, p_out = sys.argv[1:]
rows = [r for r in csv.DictReader(open(runs_csv)) if r["status"] == "PASS"]


def by(exp, sc):
    return [r for r in rows if r["exp"] == exp and r["scenario"] == sc]


def mean_sd_n(vals):
    if not vals:
        return (float("nan"), float("nan"), 0)
    m = statistics.mean(vals)
    s = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return (m, s, len(vals))


def ci95(vals):
    if len(vals) < 2:
        return (float("nan"), float("nan"))
    m, s, n = mean_sd_n(vals)
    try:
        from scipy import stats
        t = stats.t.ppf(0.975, n - 1)
    except Exception:
        t = 1.96
    half = t * s / math.sqrt(n)
    return (m - half, m + half)


def welch(a, b):
    try:
        from scipy import stats
        res = stats.ttest_ind(a, b, equal_var=False)
        return (float(res.statistic), float(res.pvalue), "Welch t-test")
    except Exception:
        return (float("nan"), float("nan"), "Welch unavailable (SciPy missing)")


def paired_t(a, b):
    try:
        from scipy import stats
        res = stats.ttest_rel(a, b)
        return (float(res.statistic), float(res.pvalue), "Paired t-test")
    except Exception:
        d = [x - y for x, y in zip(a, b)]
        n = len(d)
        obs = abs(sum(d) / n) if n else float("nan")
        vals = []
        for bits in itertools.product([-1, 1], repeat=n):
            vals.append(abs(sum(di * si for di, si in zip(d, bits)) / n))
        ge = sum(1 for v in vals if v >= obs)
        p = ge / len(vals) if vals else float("nan")
        return (float("nan"), p, "Exact sign-flip test (paired, nonparametric)")

# Experiment A
base_a = {int(r["run"]): float(r["cpi"]) for r in by("A", "baseline")}
wb_a = {int(r["run"]): float(r["cpi"]) for r in by("A", "wb")}
wc_a = {int(r["run"]): float(r["cpi"]) for r in by("A", "wc")}
wb_bw = [float(r["bw_gbps"]) for r in by("A", "wb")]
wc_bw = [float(r["bw_gbps"]) for r in by("A", "wc")]

common_a = sorted(set(base_a) & set(wb_a) & set(wc_a))
base_vals_a = [base_a[r] for r in common_a]
wb_vals_a = [wb_a[r] for r in common_a]
wc_vals_a = [wc_a[r] for r in common_a]

delta_wb = [100.0 * (wb_a[r] - base_a[r]) / base_a[r] for r in common_a]
delta_wc = [100.0 * (wc_a[r] - base_a[r]) / base_a[r] for r in common_a]
paired_diff = [dw - dc for dw, dc in zip(delta_wb, delta_wc)]

wl_t, wl_p, wl_name = welch(wb_vals_a, wc_vals_a)
pt_t, pt_p, pt_name = paired_t(delta_wb, delta_wc)

with open(a_out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["metric", "value"])
    for name, vals in [
        ("base_cpi", base_vals_a),
        ("wb_cpi", wb_vals_a),
        ("wc_cpi", wc_vals_a),
        ("wb_delta_pct", delta_wb),
        ("wc_delta_pct", delta_wc),
        ("paired_wb_minus_wc_delta_pct", paired_diff),
        ("wb_bw_gbps", wb_bw),
        ("wc_bw_gbps", wc_bw),
    ]:
        m, s, n = mean_sd_n(vals)
        lo, hi = ci95(vals)
        w.writerow([f"{name}_mean", f"{m:.6f}"])
        w.writerow([f"{name}_sd", f"{s:.6f}"])
        w.writerow([f"{name}_n", n])
        w.writerow([f"{name}_ci95_lo", f"{lo:.6f}"])
        w.writerow([f"{name}_ci95_hi", f"{hi:.6f}"])

# Experiment B
base_b = {int(r["run"]): float(r["cpi"]) for r in by("B", "baseline")}
col_b = {int(r["run"]): float(r["cpi"]) for r in by("B", "columnar_wb")}
col_bw = [float(r["bw_gbps"]) for r in by("B", "columnar_wb")]

common_b = sorted(set(base_b) & set(col_b))
base_vals_b = [base_b[r] for r in common_b]
col_vals_b = [col_b[r] for r in common_b]
delta_col = [100.0 * (col_b[r] - base_b[r]) / base_b[r] for r in common_b]

ptb_t, ptb_p, ptb_name = paired_t(col_vals_b, base_vals_b)

with open(b_out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["metric", "value"])
    for name, vals in [
        ("base_cpi", base_vals_b),
        ("columnar_wb_cpi", col_vals_b),
        ("columnar_wb_delta_pct", delta_col),
        ("columnar_wb_bw_gbps", col_bw),
    ]:
        m, s, n = mean_sd_n(vals)
        lo, hi = ci95(vals)
        w.writerow([f"{name}_mean", f"{m:.6f}"])
        w.writerow([f"{name}_sd", f"{s:.6f}"])
        w.writerow([f"{name}_n", n])
        w.writerow([f"{name}_ci95_lo", f"{lo:.6f}"])
        w.writerow([f"{name}_ci95_hi", f"{hi:.6f}"])

with open(p_out, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["experiment", "test", "statistic", "pvalue", "note"])
    w.writerow(["A", wl_name, f"{wl_t:.6f}", f"{wl_p:.6e}", "WB vs WC victim CPI"])
    w.writerow(["A", pt_name, f"{pt_t:.6f}" if not math.isnan(pt_t) else "NA", f"{pt_p:.6e}", "Paired run-level delta comparison: (WB-baseline)% vs (WC-baseline)%"])
    w.writerow(["B", ptb_name, f"{ptb_t:.6f}" if not math.isnan(ptb_t) else "NA", f"{ptb_p:.6e}", "Paired run-level CPI: columnar_wb vs baseline"])
PY

phase_log "ANALYSIS" "python3 summarize all_runs.csv" "$OUTDIR" "generated experimentA_summary.csv, experimentB_summary.csv, paired_stats.csv" "PASS"
phase_log "SUITE" "$0" "$LOG" "completed Experiment A and B" "PASS"

echo "=== ADDITIONAL EXPERIMENTS DONE $(date -Is) ==="
echo "runs_csv=$RUNS_CSV"
echo "summary_a=$A_SUMMARY"
echo "summary_b=$B_SUMMARY"
echo "paired=$PAIR_CSV"
