#!/usr/bin/env bash
# Fee-bias sweep ablation. Mutates FEE_PCT_DEFAULT from 0.02 (clean) to
# 0.020002 / 0.02002 / 0.0202, runs parity_check.py for each, and prints
# four CSV rows for the §6.3 table.

set -euo pipefail
cd "$(dirname "$0")/../.."

git diff --quiet src/lib.rs || {
    echo "src/lib.rs has uncommitted changes; aborting" >&2
    exit 1
}

run_one() {
    local label="$1" value="$2"
    python3 -c "
import sys
src = open('src/lib.rs').read()
new_src = src.replace('const FEE_PCT_DEFAULT: f64 = 0.02;',
                      'const FEE_PCT_DEFAULT: f64 = $value;')
if new_src == src:
    print('FEE_PCT_DEFAULT marker not found', file=sys.stderr); sys.exit(1)
open('src/lib.rs', 'w').write(new_src)
"
    cargo build --release >/dev/null 2>&1
    local out summary max_rel ledger_out l_n
    set +e
    # Full output (not tail -1): the per-field "rel=" lines feed the max-rel calc.
    out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
    # Also diff the per-trade ledger so the Table 3 Ledger column is script-produced.
    ledger_out=$(python3 tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
    set -e
    git checkout -- src/lib.rs
    summary=$(echo "$out" | grep -E 'PARITY (OK|FAIL)' | tail -1)
    l_n=$(echo "$ledger_out" | grep -oE 'LEDGER PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true); l_n=${l_n:-0}
    # Max relative deviation across parity_check.py's "rel={rel:6.2%}" fields.
    max_rel=$(echo "$out" | python3 -c "
import re, sys
rels = [float(x) for x in re.findall(r'rel=\s*([0-9.]+)%', sys.stdin.read())]
print(f'{max(rels):.2f}' if rels else 'n/a')
")
    if [[ "$summary" == *"OK"* ]]; then
        echo "$label, metric=0, ledger=${l_n}, max_rel=<5e-5, OK"
    else
        local n
        n=$(echo "$summary" | grep -oE 'PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true)
        echo "$label, metric=${n:-0}, ledger=${l_n}, max_rel=${max_rel}, FAIL"
    fi
}

# Clean baseline (no edit needed; assert)
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
[[ "$out" == *"OK"* ]] && echo "fee_0pct, metric=0, ledger=0, max_rel=<5e-5, OK" || \
    echo "fee_0pct, ?, ?, UNEXPECTED_FAIL"

run_one "fee_0.01pct" "0.020002"
run_one "fee_0.1pct"  "0.02002"
run_one "fee_1pct"    "0.0202"

cargo build --release >/dev/null 2>&1
