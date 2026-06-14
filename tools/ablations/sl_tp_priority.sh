#!/usr/bin/env bash
# SL-vs-TP priority inversion ablation. The engine's intrabar tie-break
# (when both stop-loss and take-profit are touched on the same bar) is
# encoded at lib.rs:471 as `if hit_sl && hit_tp { hit_tp = false; }` —
# i.e. SL wins. This script inverts the rule (TP wins on a tie) and
# checks whether parity_check + parity_ledger catch the change.

set -euo pipefail
cd "$(dirname "$0")/../.."

git diff --quiet src/lib.rs || {
    echo "src/lib.rs has uncommitted changes; aborting" >&2
    exit 1
}

python3 -c "
import sys
src = open('src/lib.rs').read()
old1 = 'let hit_sl = if open_pos == 1 { bars[idx].low <= sl_pr } else { bars[idx].high >= sl_pr };'
new1 = 'let mut hit_sl = if open_pos == 1 { bars[idx].low <= sl_pr } else { bars[idx].high >= sl_pr };  // ABLATION mutable'
old2 = 'if hit_sl && hit_tp { hit_tp = false; }'
new2 = 'if hit_sl && hit_tp { hit_sl = false; }  // ABLATION: TP wins on tie'
if old1 not in src or old2 not in src:
    print('SL/TP marker lines not found; aborting', file=sys.stderr); sys.exit(1)
src = src.replace(old1, new1).replace(old2, new2)
open('src/lib.rs', 'w').write(src)
"

cargo build --release >/dev/null 2>&1
# parity scripts exit non-zero on mismatch; capture without aborting.
# Capture FULL output (not tail -1) so the per-field "rel=" lines feed max-rel.
set +e
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

echo "sl_tp_priority, metric=${m_n:-0}, ledger=${l_n:-0}, max_rel=${max_rel}"
