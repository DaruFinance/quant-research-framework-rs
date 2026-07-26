#!/usr/bin/env python3
"""Tabulate the parity surface from a sweep transcript directory.

Reads the per-check logs written by tools/sweep_all.sh and reports, per
surface and dataset, how many comparisons ran and how many passed. The
totals are the numbers a write-up should quote, and separating metric
breadth from per-trade depth keeps a large ledger expansion from being
mistaken for independent coverage.

    python tools/parity_counts.py [.sweep]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

FIELD_RE = re.compile(r"^\s+(trades|roi|pf|sharpe|win_rate|exp|max_dd):", re.M)
HEADER_RE = re.compile(r"Metric comparison \((\d+) tags x (\d+) fields")
LEDGER_RE = re.compile(r"all (\d[\d,]*) fields across (\d[\d,]*) common trades")
COMBO_RE = re.compile(r"COMBO PARITY OK \((\d+) stages x (\d+) fields")
OK_RE = re.compile(r"PARITY OK|LEDGER PARITY OK|COMBO PARITY OK")
DIFF_RE = re.compile(r"PARITY DIFF: (\d+)|LEDGER PARITY FAIL: (\d+)")


def n(text: str) -> int:
    return int(text.replace(",", ""))


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".sweep")
    if not root.is_dir():
        sys.exit(f"no transcript directory at {root}")

    metric_rows, ledger_rows, other = [], [], []
    for log in sorted(root.glob("*.log")):
        name = log.stem
        for pfx in ("parity_check_", "parity_regime_", "parity_forex_",
                    "parity_ledger_", "parity_combo_"):
            if name.startswith(pfx):
                name = f"{pfx[:-1]} [{name[len(pfx):]}]"
                break
        text = log.read_text(errors="replace")
        passed = bool(OK_RE.search(text))
        cm = COMBO_RE.search(text)
        if cm:
            stages, fields = int(cm.group(1)), int(cm.group(2))
            metric_rows.append((name, stages, stages * fields, passed))
            continue
        m = HEADER_RE.search(text)
        if m:
            checks = len(FIELD_RE.findall(text))
            metric_rows.append((name, int(m.group(1)), checks, passed))
            continue
        lm = LEDGER_RE.search(text)
        if lm:
            ledger_rows.append((name, n(lm.group(2)), n(lm.group(1)), passed))
            continue
        other.append((name, passed))

    print(f"{'surface / dataset':46} {'stages':>7} {'checks':>8} {'result':>7}")
    print("-" * 72)
    mt = 0
    for name, stages, checks, ok in metric_rows:
        mt += checks if ok else 0
        print(f"{name:46} {stages:>7} {checks:>8} {'OK' if ok else 'DIFF':>7}")
    print(f"{'metric breadth (passing)':46} {'':>7} {mt:>8}")

    if ledger_rows:
        print()
        print(f"{'per-trade ledger':46} {'trades':>7} {'fields':>8} {'result':>7}")
        print("-" * 72)
        lt = 0
        for name, trades, fields, ok in ledger_rows:
            lt += fields if ok else 0
            print(f"{name:46} {trades:>7} {fields:>8} {'OK' if ok else 'DIFF':>7}")
        print(f"{'ledger depth (passing)':46} {'':>7} {lt:>8}")
    else:
        lt = 0

    if other:
        print()
        print("component and guard checks (pass/fail only, not counted above):")
        for name, ok in other:
            print(f"  {'OK  ' if ok else 'note'}  {name}")

    print()
    print(f"metric breadth : {mt}")
    print(f"ledger depth   : {lt}")
    print(f"combined       : {mt + lt}")
    print("Quote breadth and depth separately. Ledger fields come from one run "
          "each and are highly correlated, so the combined figure is not a "
          "count of independent observations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
