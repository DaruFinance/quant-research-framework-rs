#!/usr/bin/env bash
# Speed-up decomposition control: is the Python-vs-Rust gap algorithmic or is it
# a constant language factor?
#
# compute_ema carries an env-gated alternative that recomputes the EMA the
# materialised O(n*w) way instead of the shipped O(n) recursion, with the two
# numerically equivalent. Timing the shipped Rust, the materialised Rust and
# Python on the same workload separates the two candidate explanations.
#
# The reading: if the gap were algorithmic, the materialised Rust variant would
# land near Python. It does not, and dialling QRF_ROLLING_MULT moves the
# apparent "algorithmic" factor around freely, which shows the factor is a
# property of the chosen window rather than of the engines. At a wide window the
# materialised Rust is slower than pandas' vectorised ewm, so pandas is paying
# no O(n*w) cost to decompose against.
#
# Requires QRF_PY_DIR (or a sibling ../quant-research-framework checkout).

set -euo pipefail
cd "$(dirname "$0")/../.."

PY_DIR="${QRF_PY_DIR:-../quant-research-framework}"
CSV="${CSV:-data/SOLUSDT_1h.csv}"
MULTS="${MULTS:-5 10 40}"

[ -f "$PY_DIR/backtester/__init__.py" ] || {
    echo "python reference not found at $PY_DIR; set QRF_PY_DIR" >&2; exit 2; }

cargo build --release >/dev/null 2>&1

time_cmd() {
    local start end
    start=$(python3 -c 'import time;print(time.perf_counter())')
    "$@" >/dev/null 2>&1
    end=$(python3 -c 'import time;print(time.perf_counter())')
    python3 -c "print(f'{$end - $start:.3f}')"
}

echo "variant, window_mult, seconds"

t_shipped=$(QRF_ROLLING= time_cmd ./target/release/backtester "$CSV")
echo "rust_shipped_on, -, $t_shipped"

for mult in $MULTS; do
    t=$(QRF_ROLLING=materialized QRF_ROLLING_MULT="$mult" time_cmd \
        ./target/release/backtester "$CSV")
    echo "rust_materialized, $mult, $t"
done

t_py=$(cd "$PY_DIR" && BT_CSV="$OLDPWD/$CSV" MPLBACKEND=Agg \
    time_cmd python3 -m backtester)
echo "python_reference, -, $t_py"

# Equivalence check: the gated path must agree with the shipped one, otherwise
# the timing comparison is measuring two different computations.
python3 - "$CSV" <<'PY'
import subprocess, sys, os, re
csv = sys.argv[1]
def metrics(env):
    e = dict(os.environ); e.update(env)
    out = subprocess.run(["./target/release/backtester", csv], env=e,
                         capture_output=True, text=True).stdout
    return re.findall(r"ROI:\s*\$?(-?[\d,]+\.\d+)", out)
a = metrics({"QRF_ROLLING": ""})
b = metrics({"QRF_ROLLING": "materialized", "QRF_ROLLING_MULT": "40"})
if not a:
    sys.exit("no ROI lines parsed; cannot verify equivalence")
bad = [(x, y) for x, y in zip(a, b)
       if abs(float(x.replace(",", "")) - float(y.replace(",", "")))
       > 1e-3 * max(abs(float(x.replace(",", ""))), 1.0)]
print(f"equivalence, {len(a)} ROI values compared, "
      f"{'OK' if not bad else f'{len(bad)} DIVERGENT'}")
sys.exit(1 if bad else 0)
PY
