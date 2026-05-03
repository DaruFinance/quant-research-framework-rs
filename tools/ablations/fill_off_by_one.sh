#!/usr/bin/env bash
# Off-by-one fill timing ablation. Replaces `bars[idx].open` with
# `bars[(idx + 1).min(bars.len() - 1)].open` in backtest_core, so every
# fill prices off the *next* bar instead of the current one. Runs the
# default-config parity check and prints the row in Table 3 format.

set -euo pipefail
cd "$(dirname "$0")/../.."

git diff --quiet src/lib.rs || {
    echo "src/lib.rs has uncommitted changes; aborting" >&2
    exit 1
}

python3 -c "
import sys, re
src = open('src/lib.rs').read()
old = '        let price_open = bars[idx].open;'
new = '        let price_open = bars[(idx + 1).min(bars.len() - 1)].open;  // ABLATION'
if old not in src:
    print('marker line not found; aborting', file=sys.stderr); sys.exit(1)
open('src/lib.rs', 'w').write(src.replace(old, new))
"

cargo build --release >/dev/null 2>&1
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)

git checkout -- src/lib.rs
cargo build --release >/dev/null 2>&1

# Parse "PARITY FAIL: N mismatches outside ..." or "PARITY OK"
if [[ "$out" == *"OK"* ]]; then
    echo "fill_off_by_one, 0, <5e-5, OK"
else
    n=$(echo "$out" | grep -oE '[0-9]+ mismatches' | grep -oE '[0-9]+')
    echo "fill_off_by_one, ${n:-?}, <run for max-rel>, FAIL"
fi
