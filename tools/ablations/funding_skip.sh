#!/usr/bin/env bash
# Missed funding accrual ablation. Comments out the `funding_acc += fee_f`
# line in backtest_core, so the per-bar funding fee is computed but never
# accumulated into per-trade PnL. Runs default-config parity check.

set -euo pipefail
cd "$(dirname "$0")/../.."

# Restore on any exit path. Without this an aborted run leaves src/lib.rs
# patched or, worse, leaves target/release/backtester built from patched
# source, silently poisoning every later parity run.
restore_source() {
    git checkout -- src/lib.rs 2>/dev/null || true
    cargo build --release >/dev/null 2>&1 || true
}
trap restore_source EXIT

max_rel() {   # largest "rel= N%" in a parity transcript, or "-" if none
    printf '%s\n' "$1" | grep -oE 'rel=[[:space:]]*[0-9.]+%' \
        | grep -oE '[0-9.]+' | sort -g | tail -1 || true
}

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
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
ledger_out=$(python3 tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
set -e

git checkout -- src/lib.rs
cargo build --release >/dev/null 2>&1

if [[ "$out" == *"PARITY OK"* ]]; then
    echo "funding_skip, metric=0, ledger=0, <5e-5, OK"
else
    n=$(echo "$out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
    mr=$(max_rel "$out")
    l_n=$(printf '%s\n' "$ledger_out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
    echo "funding_skip, metric=${n:-?}, ledger=${l_n:-0}, ${mr:-?}%, FAIL"
fi
