#!/usr/bin/env bash
# Full cross-language validation sweep: every parity surface on every dataset
# it supports, plus both test suites, the consistency guard, the benchmark
# drift check and the cross-architecture golden check.
#
#   QRF_PY_DIR=/path/to/quant-research-framework bash tools/sweep_all.sh
#
# Writes one line per check to stdout and a full transcript per check under
# $LOGDIR. Exit 0 only when every check passes.

set -u
RS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY_DIR="${QRF_PY_DIR:?set QRF_PY_DIR to the Python reference checkout}"
LOGDIR="${LOGDIR:-$RS_DIR/.sweep}"
TOL="${TOL:-0.001}"
mkdir -p "$LOGDIR"

pass=0; fail=0; results=""

run() {
  local name="$1"; shift
  local log="$LOGDIR/${name//[^A-Za-z0-9_.-]/_}.log"
  local t0 t1
  t0=$(date +%s)
  if "$@" >"$log" 2>&1; then
    t1=$(date +%s)
    printf '  PASS  %-42s %4ds\n' "$name" "$((t1-t0))"
    results="${results}PASS  ${name}\n"; pass=$((pass+1))
  else
    t1=$(date +%s)
    printf '  FAIL  %-42s %4ds   -> %s\n' "$name" "$((t1-t0))" "$log"
    results="${results}FAIL  ${name}\n"; fail=$((fail+1))
  fi
}

cd "$RS_DIR"
export QRF_PY_DIR="$PY_DIR"
echo "Rust : $RS_DIR"
echo "Py   : $PY_DIR"
echo "Tol  : $TOL"
echo

echo "== metric surfaces, every supported dataset =="
for ds in SOLUSDT_1h BTCUSDT_30m DOGEUSDT_30m SYNTH_100k; do
  run "parity_check/$ds" python3 tools/parity_check.py --csv "data/$ds.csv" --tol "$TOL"
done
for ds in SOLUSDT_1h BTCUSDT_30m DOGEUSDT_30m; do
  run "parity_regime/$ds" python3 tools/parity_regime.py --csv "data/$ds.csv" --tol "$TOL"
done
for ds in EURUSD_1h USDJPY_1h; do
  run "parity_forex/$ds" python3 tools/parity_forex.py --csv "data/$ds.csv" --tol "$TOL"
done
for ds in SOLUSDT_1h BTCUSDT_30m; do
  run "parity_ledger/$ds" python3 tools/parity_ledger.py --csv "data/$ds.csv" --tol "$TOL"
done
for ds in EURUSD_1h SOLUSDT_1h; do
  run "parity_combo/$ds" python3 tools/parity_combo.py --csv "data/$ds.csv" --tol "$TOL"
done

echo
echo "== component surfaces =="
run "parity_indicators"    python3 tools/parity_indicators.py --tol "$TOL"
run "parity_volume"        python3 tools/parity_volume.py --tol "$TOL"
run "parity_surface"       python3 tools/parity_surface.py --tol "$TOL"
run "parity_dsr"           python3 tools/parity_dsr.py --tol "$TOL"
run "parity_pbo"           python3 tools/parity_pbo.py --tol "$TOL"
run "parity_multitest"     python3 tools/parity_multitest.py --tol "$TOL"
run "parity_overfit_lines" python3 tools/parity_overfit_lines.py
run "parity_panel"         python3 tools/parity_panel.py
run "parity_pairs"         python3 tools/parity_pairs.py --tol "$TOL"
run "parity_carry"         python3 tools/parity_carry.py --tol "$TOL"

echo
echo "== determinism, suites and guards =="
run "parity_arch/x86_64 goldens" python3 tools/parity_arch.py --bin target/release/backtester
run "benchmark --check"          python3 tools/benchmark.py --check
run "check_consistency"          python3 tools/check_consistency.py
run "cargo test"                 cargo test --release --jobs 1 --features "panel,pairs,carry,dsr,indicators"
run "pytest (python reference)"  python3 -m pytest "$PY_DIR/tests" -q -x --no-header

echo
echo "=================== sweep summary ==================="
printf '%b' "$results"
echo "-----------------------------------------------------"
echo "  $pass passed, $fail failed   (logs in $LOGDIR)"
[ "$fail" -eq 0 ] && echo "  SWEEP OK" || echo "  SWEEP FAIL"
exit $([ "$fail" -eq 0 ] && echo 0 || echo 1)
