#!/usr/bin/env bash
# Funding-sign inversion ablation. Per-bar funding is computed at
# lib.rs:433 as `let fee_f = qty * bars[idx].open * funding_rate` and
# accumulated via `funding_acc += fee_f` on line 434, then subtracted
# from per-trade PnL when the trade closes. This script flips the
# accumulation sign so funding becomes a credit instead of a debit.

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
            let fee_f = qty * bars[idx].open * funding_rate;
            funding_acc -= fee_f;  // ABLATION: sign flip
            let last = equity_list.len() - 1;
            equity_list[last] += fee_f;  // ABLATION: sign flip
        }'''
if old not in src:
    print('funding marker block not found; aborting', file=sys.stderr); sys.exit(1)
open('src/lib.rs', 'w').write(src.replace(old, new))
"

cargo build --release >/dev/null 2>&1
set +e
metric_out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
ledger_out=$(python3 tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1 | tail -1)
set -e

git checkout -- src/lib.rs
cargo build --release >/dev/null 2>&1

m_n=$(echo "$metric_out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
l_n=$(echo "$ledger_out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)

max_rel=$(printf '%s\n' "$metric_out" | grep -oE 'rel=[[:space:]]*[0-9.]+%' | grep -oE '[0-9.]+' | sort -g | tail -1 || true)
echo "funding_sign, metric=${m_n:-0}, ledger=${l_n:-0}, max_rel=${max_rel:-?}%"
