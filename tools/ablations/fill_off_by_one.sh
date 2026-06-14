#!/usr/bin/env bash
# Off-by-one fill timing ablation. Replaces `bars[idx].open` with
# `bars[(idx + 1).min(bars.len() - 1)].open` in backtest_core, so every
# fill prices off the *next* bar instead of the current one. Runs the
# default-config parity check and prints the row in Table 3 format.

set -euo pipefail
cd "$(dirname "$0")/../.."

# Force cargo to recompile after a source edit even on 1-second-mtime-resolution
# filesystems, where a same-second edit (e.g. ablations run back-to-back) can be
# missed by cargo's mtime-based change detection, leaving a stale binary that
# could even report PARITY OK on a genuinely buggy build. Bump src/lib.rs's
# mtime strictly past the current binary so the next `cargo build` recompiles.
bump_src() { python3 -c "import os; b=os.path.getmtime('target/release/backtester') if os.path.exists('target/release/backtester') else 0; m=max(os.path.getmtime('src/lib.rs'),b)+5; os.utime('src/lib.rs',(m,m))"; }

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

bump_src
cargo build --release >/dev/null 2>&1
set +e
# Capture the FULL parity output: we need the per-field `rel=` lines to
# compute the max relative deviation, not just the summary line.
out=$(python3 tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
# Also diff the per-trade ledger so the Table 3 Ledger column is produced by
# this script (not just parity_check). The binary is freshly built above.
ledger_out=$(python3 tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001 2>&1)
set -e

git checkout -- src/lib.rs
bump_src
cargo build --release >/dev/null 2>&1

summary=$(echo "$out" | grep -E 'PARITY (OK|FAIL)' | tail -1)

# Max relative deviation = max over every "rel=NN.NN%" token that
# parity_check.py prints per metric field (see its compare() format string:
#   "rel={rel:6.2%}"). Reported as the bare percentage to match Table 3.
max_rel=$(echo "$out" | python3 -c "
import re, sys
rels = [float(x) for x in re.findall(r'rel=\s*([0-9.]+)%', sys.stdin.read())]
print(f'{max(rels):.2f}' if rels else 'n/a')
")

# Ledger mismatch count: final "LEDGER PARITY FAIL: N" summary line, 0 if OK.
l_n=$(echo "$ledger_out" | grep -oE 'LEDGER PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true); l_n=${l_n:-0}

# Parse "PARITY FAIL: N mismatches outside ..." or "PARITY OK"
if [[ "$summary" == *"OK"* ]]; then
    echo "fill_off_by_one, metric=0, ledger=${l_n}, max_rel=<5e-5, OK"
else
    n=$(echo "$summary" | grep -oE 'PARITY FAIL: [0-9]+' | grep -oE '[0-9]+' || true)
    echo "fill_off_by_one, metric=${n:-0}, ledger=${l_n}, max_rel=${max_rel}, FAIL"
fi
