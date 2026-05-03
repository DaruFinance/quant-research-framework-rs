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
    local out
    out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
    git checkout -- src/lib.rs
    if [[ "$out" == *"OK"* ]]; then
        echo "$label, 0, <5e-5, OK"
    else
        local n
        n=$(echo "$out" | grep -oE '[0-9]+ mismatches' | grep -oE '[0-9]+')
        echo "$label, ${n:-?}, <run for max-rel>, FAIL"
    fi
}

# Clean baseline (no edit needed; assert)
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
[[ "$out" == *"OK"* ]] && echo "fee_0pct, 0, <5e-5, OK" || \
    echo "fee_0pct, ?, ?, UNEXPECTED_FAIL"

run_one "fee_0.01pct" "0.020002"
run_one "fee_0.1pct"  "0.02002"
run_one "fee_1pct"    "0.0202"

cargo build --release >/dev/null 2>&1
