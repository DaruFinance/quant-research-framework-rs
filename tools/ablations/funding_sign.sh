#!/usr/bin/env bash
# Funding-sign inversion ablation. Per-bar funding is computed at
# lib.rs:433 as `let fee_f = qty * bars[idx].open * funding_rate` and
# accumulated via `funding_acc += fee_f` on line 434, then subtracted
# from per-trade PnL when the trade closes. This script flips the
# accumulation sign so funding becomes a credit instead of a debit.

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
# Full output (not tail -1) so the per-field "rel=" lines are available for max-rel.
metric_out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
ledger_out=$(python3 tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
set -e

git checkout -- src/lib.rs
cargo build --release >/dev/null 2>&1

# Parse the SUMMARY lines only. parity_ledger prints per-field "[FAIL] N field
# mismatches" lines before the final "LEDGER PARITY FAIL: M mismatches" total,
# so a bare "[0-9]+ mismatches" grep grabbed the wrong (first) number. Anchor on
# the summary prefixes instead; "OK" lines carry no number, so default to 0.
m_n=$(echo "$metric_out" | grep -oE 'PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true); m_n=${m_n:-0}
l_n=$(echo "$ledger_out" | grep -oE 'LEDGER PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true); l_n=${l_n:-0}

# Max relative deviation on the metric surface (parity_check.py "rel={rel:6.2%}").
max_rel=$(echo "$metric_out" | python3 -c "
import re, sys
rels = [float(x) for x in re.findall(r'rel=\s*([0-9.]+)%', sys.stdin.read())]
print(f'{max(rels):.2f}' if rels else 'n/a')
")

echo "funding_sign, metric=${m_n:-0}, ledger=${l_n:-0}, max_rel=${max_rel}"
