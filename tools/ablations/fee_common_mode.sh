#!/usr/bin/env bash
# Common-mode control for the parity oracle: the negative control that bounds
# what a self-consistency diff cannot see.
#
# Applies the SAME +1% fee bias to BOTH ports (Rust FEE_PCT_DEFAULT and Python
# FEE_PCT), so the two implementations charge an identical wrong fee. Because
# the diff only ever compares the two engines against each other, a fault
# present in both is invisible to it at any tolerance, and this row is expected
# to read PARITY OK. Contrast fee_bias.sh, whose Rust-only 1% row fails loudly.
#
# Requires QRF_PY_DIR (or a sibling ../quant-research-framework checkout).

set -euo pipefail
cd "$(dirname "$0")/../.."

RS=src/lib.rs
PY_DIR="${QRF_PY_DIR:-../quant-research-framework}"
PY="$PY_DIR/backtester/__init__.py"
VAL=0.0202   # 0.02 * 1.01, a +1% relative fee bias

[ -f "$PY" ] || { echo "python reference not found at $PY; set QRF_PY_DIR" >&2; exit 2; }

git diff --quiet "$RS" || { echo "$RS has uncommitted changes; aborting" >&2; exit 1; }
git -C "$PY_DIR" diff --quiet -- backtester/__init__.py || {
    echo "python reference has uncommitted changes; aborting" >&2; exit 1; }

PY_BAK=$(mktemp)
cp "$PY" "$PY_BAK"
restore() {
    git checkout -- "$RS"
    cp "$PY_BAK" "$PY"
    rm -f "$PY_BAK"
    cargo build --release >/dev/null 2>&1 || true
}
trap restore EXIT

python3 - "$RS" "$PY" "$VAL" <<'PY'
import sys
rs_path, py_path, val = sys.argv[1], sys.argv[2], sys.argv[3]
rs = open(rs_path).read()
new = rs.replace("const FEE_PCT_DEFAULT: f64 = 0.02;",
                 f"const FEE_PCT_DEFAULT: f64 = {val};")
if new == rs:
    sys.exit("FEE_PCT_DEFAULT marker not found in the Rust source")
open(rs_path, "w").write(new)

py = open(py_path, newline="").read()
new = py.replace("FEE_PCT             = 0.02  ",
                 f"FEE_PCT             = {val}")
if new == py:
    sys.exit("FEE_PCT marker not found in the Python reference")
open(py_path, "w", newline="").write(new)
PY

cargo build --release >/dev/null 2>&1

set +e
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
set -e

if [[ "$out" == *"OK"* ]]; then
    echo "fee_1pct_both_ports, 0, <5e-5, OK (blind spot confirmed)"
else
    echo "fee_1pct_both_ports, ?, ?, UNEXPECTED_FAIL: $out"
    exit 1
fi
