#!/usr/bin/env bash
# artifact/reproduce.sh — End-to-end driver for directory-tax-spr artifact
#
# Runs Phases 0–5 and reproduces all figures.
# Requires root for one-time setup (sudo env/setup.sh).
#
# Usage:
#   artifact/reproduce.sh [--skip-setup] [--quick N]
#
#   --skip-setup  Skip env/setup.sh (if already run)
#   --quick N     Use N trials instead of n=30/10 (for a fast smoke test)
#
# Expected runtime: ~3–4 hours for full run.
# Figures: results/figures/*.pdf
# Main tables: results/processed/02_matrix.csv, 05_stats_table.md

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

SKIP_SETUP=0
QUICK_N=""

for arg in "$@"; do
    case "$arg" in
        --skip-setup) SKIP_SETUP=1 ;;
        --quick)      shift; QUICK_N="$1" ;;
        --quick=*)    QUICK_N="${arg#--quick=}" ;;
    esac
done

echo "=============================================="
echo "  directory-tax-spr artifact reproduce.sh"
echo "  Date: $(date --iso-8601=seconds)"
echo "  Host: $(hostname)"
echo "  User: $(id -un)"
echo "=============================================="

# ── Phase 0: Environment Setup ───────────────────────────────────────────────
echo ""
echo "=== Phase 0: Environment Setup ==="
if [[ "$SKIP_SETUP" -eq 0 ]]; then
    echo "Running sudo env/setup.sh (password may be required once)..."
    sudo env/setup.sh
else
    echo "  --skip-setup: skipping setup.sh"
fi

echo ""
echo "Running env/validate.sh..."
if ! env/validate.sh; then
    echo "GATE FAILED: Environment not ready. Fix failures above and retry."
    echo "Most likely fix: sudo env/setup.sh"
    exit 1
fi
echo "Phase 0 gate: PASS"

# ── Build ─────────────────────────────────────────────────────────────────────
echo ""
echo "=== Building benchmarks ==="
make -C bench/ -j4
echo "Build: OK"

# ── Install Python dependencies ───────────────────────────────────────────────
echo ""
echo "=== Checking Python dependencies ==="
pip3 install -q numpy scipy statsmodels matplotlib pandas 2>&1 | tail -3

# ── Phase 1: Calibration ──────────────────────────────────────────────────────
echo ""
echo "=== Phase 1: Bandwidth Calibration ==="
python3 exp/01_calibration.py
echo "Phase 1 complete."

# ── Phase 2: Main Matrix ──────────────────────────────────────────────────────
echo ""
echo "=== Phase 2: Four-Condition Matrix ==="
python3 exp/02_matrix.py
echo "Phase 2 complete."

# ── Phase 3: PMU Sweep ────────────────────────────────────────────────────────
echo ""
echo "=== Phase 3: PMU Sweep (SF Eviction Rate) ==="
python3 exp/03_pmu_sweep.py
echo "Phase 3 complete."

# ── Phase 4a: WSS Sweep ───────────────────────────────────────────────────────
echo ""
echo "=== Phase 4a: WSS Sweep ==="
python3 exp/04_wss_sweep.py
echo "Phase 4a complete."

# ── Phase 4b: Aggressor Sweep ─────────────────────────────────────────────────
echo ""
echo "=== Phase 4b: Aggressor Count Sweep ==="
python3 exp/05_aggressor_sweep.py
echo "Phase 4b complete."

# ── Phase 5: Statistical Analysis ────────────────────────────────────────────
echo ""
echo "=== Phase 5: Statistical Analysis ==="
python3 analysis/stats.py
echo "Phase 5 complete."

# ── Generate Figures ──────────────────────────────────────────────────────────
echo ""
echo "=== Generating Figures ==="
python3 analysis/plot_matrix.py
python3 analysis/plot_mechanism.py
python3 analysis/plot_sweeps.py

echo ""
echo "=============================================="
echo "  Artifact reproduction complete!"
echo "  Figures:    results/figures/"
echo "  Main table: results/processed/05_stats_table.md"
echo "  Mechanism:  results/processed/03_mechanism_findings.md"
echo "  Phase logs: results/processed/0*_phase_report.md"
echo "=============================================="

# ── Verify against expected results ──────────────────────────────────────────
echo ""
echo "=== Comparing against EXPECTED.md ==="
echo "  (Manual review required — see artifact/EXPECTED.md)"
ls results/figures/
