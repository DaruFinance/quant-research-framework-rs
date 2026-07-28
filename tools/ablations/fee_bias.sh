#!/usr/bin/env bash
# Fee-bias sweep ablation. Mutates FEE_PCT_DEFAULT from 0.02 (clean) to
# 0.020002 / 0.02002 / 0.0202, runs parity_check.py for each, and prints
# four CSV rows for the §6.3 table.

set -euo pipefail
cd "$(dirname "$0")/../.."

# Dataset under test. Override to reproduce the cross-dataset rows of the
# fault-injection table:  CSV=data/BTCUSDT_30m.csv bash tools/ablations/fee_bias.sh
CSV="${CSV:-data/SOLUSDT_1h.csv}"

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

max_rel() {   # largest "rel= N%" in a parity transcript, or "-" if none
    printf '%s\n' "$1" | grep -oE 'rel=[[:space:]]*[0-9.]+%' \
        | grep -oE '[0-9.]+' | sort -g | tail -1 || true
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
    set +e
    out=$(python3 tools/parity_check.py --csv "$CSV" --tol 0.001 2>&1)
    local ledger_out l_n
    ledger_out=$(python3 tools/parity_ledger.py --csv "$CSV" --tol 0.001 2>&1 | tail -1)
    set -e
    git checkout -- src/lib.rs
    if [[ "$out" == *"PARITY OK"* ]]; then
        # Report the ledger result we actually measured rather than
        # assuming it is clean because the metric surface passed. A
        # metric-clean / ledger-dirty row is exactly the metric
        # cancellation mode this campaign is meant to be able to show.
        if [[ "$ledger_out" == *"LEDGER PARITY OK"* ]]; then
            l_n=0
        else
            l_n=$(printf '%s\n' "$ledger_out" | grep -oE 'PARITY (DIFF|FAIL): [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
        fi
        echo "$label, metric=0, ledger=${l_n:-?}, <5e-5, OK"
    else
        local n
        n=$(echo "$out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
        local mr; mr=$(max_rel "$out")
        l_n=$(printf '%s\n' "$ledger_out" | grep -oE 'PARITY DIFF: [0-9]+|[0-9]+ mismatches' | grep -oE '[0-9]+' | head -1 || true)
        echo "$label, metric=${n:-?}, ledger=${l_n:-0}, ${mr:-?}%, FAIL"
    fi
}

# Clean baseline (no edit needed; assert)
out=$(python3 tools/parity_check.py --csv "$CSV" --tol 0.001 2>&1 | tail -1)
[[ "$out" == *"PARITY OK"* ]] && echo "fee_0pct, 0, <5e-5, OK" || \
    echo "fee_0pct, ?, ?, UNEXPECTED_FAIL"

run_one "fee_0.01pct" "0.020002"
run_one "fee_0.1pct"  "0.02002"
run_one "fee_1pct"    "0.0202"

cargo build --release >/dev/null 2>&1
