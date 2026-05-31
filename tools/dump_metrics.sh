#!/usr/bin/env bash
# Dump the deterministic metric block of the reference binary on every
# bundled dataset, stripping the non-deterministic timing lines, into one
# file per dataset. Used by tools/parity_arch.py (roadmap item 06) to build
# the x86_64 goldens and to capture an aarch64 binary's output for a
# byte-identical cross-architecture comparison.
#
# Usage:
#   tools/dump_metrics.sh <binary> <outdir> [runner...]
#
#   <binary>   path to the compiled `backtester` binary
#   <outdir>   directory to write <dataset>.txt files into (created)
#   [runner]   optional launcher prefix, e.g. `qemu-aarch64-static` to run
#              an aarch64 binary under user-mode QEMU on an x86_64 host
#
# A "metric line" is any line matching the same shape parity_check.py's
# LINE_RE matches: `<tag> | Trades:.. ROI:.. PF:.. Shp:.. Win:.. Exp:..
# MaxDD:..`. Those fields are fully deterministic; wall-clock/load lines
# are not and are excluded.
set -euo pipefail

BIN="${1:?usage: dump_metrics.sh <binary> <outdir> [runner...]}"
OUTDIR="${2:?usage: dump_metrics.sh <binary> <outdir> [runner...]}"
shift 2
RUNNER=("$@")

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUTDIR"

DATASETS=(
  SOLUSDT_1h
  BTCUSDT_30m
  DOGEUSDT_30m
  EURUSD_1h
  USDJPY_1h
  SYNTH_100k
)

# Grep filter: the metric-block lines (a tag, then "| Trades: .. MaxDD: ..").
METRIC_RE='\|[[:space:]]*Trades:[[:space:]]*-?[0-9]+[[:space:]]+ROI:'

for ds in "${DATASETS[@]}"; do
  csv="$REPO_ROOT/data/${ds}.csv"
  if [[ ! -f "$csv" ]]; then
    echo "skip ${ds}: $csv not found" >&2
    continue
  fi
  out="$OUTDIR/${ds}.txt"
  "${RUNNER[@]}" "$BIN" "$csv" | grep -E "$METRIC_RE" > "$out"
  echo "wrote $out ($(wc -l < "$out") metric lines)"
done
