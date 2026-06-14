#!/usr/bin/env bash
# Missed funding accrual ablation. Comments out the `funding_acc += fee_f`
# line in backtest_core, so the per-bar funding fee is computed but never
# accumulated into per-trade PnL. Runs default-config parity check.

set -euo pipefail
cd "$(dirname "$0")/../.."

git diff --quiet src/lib.rs || {
    echo "src/lib.rs has uncommitted changes; aborting" >&2
    exit 1
}

python3 -c "
import sys
src = open('src/lib.rs').read()
old = '''        if open_pos != 0 && funding_mask[idx] {
            let fee_f = qty * bars[idx].open * funding_rate;
            funding_acc += fee_f;
            let last = equity_list.len() - 1;
            equity_list[last] -= fee_f;
        }'''
new = '''        if open_pos != 0 && funding_mask[idx] {
            let _fee_f = qty * bars[idx].open * funding_rate;
            // ABLATION: funding_acc += _fee_f; equity_list[last] -= _fee_f;
        }'''
if old not in src:
    print('marker block not found; aborting', file=sys.stderr); sys.exit(1)
open('src/lib.rs', 'w').write(src.replace(old, new))
"

cargo build --release >/dev/null 2>&1
set +e
# Capture the FULL parity output so the per-field `rel=` lines are available
# for the max-relative-deviation calculation.
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
set -e

git checkout -- src/lib.rs
cargo build --release >/dev/null 2>&1

summary=$(echo "$out" | grep -E 'PARITY (OK|FAIL)' | tail -1)

# Max relative deviation across every "rel=NN.NN%" metric line emitted by
# parity_check.py (compare() prints "rel={rel:6.2%}"). Bare percent for Table 3.
max_rel=$(echo "$out" | python3 -c "
import re, sys
rels = [float(x) for x in re.findall(r'rel=\s*([0-9.]+)%', sys.stdin.read())]
print(f'{max(rels):.2f}' if rels else 'n/a')
")

if [[ "$summary" == *"OK"* ]]; then
    echo "funding_skip, 0, <5e-5, OK"
else
    n=$(echo "$summary" | grep -oE '[0-9]+ mismatches' | grep -oE '[0-9]+')
    echo "funding_skip, ${n:-?}, ${max_rel}, FAIL"
fi
