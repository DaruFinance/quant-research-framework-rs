#!/usr/bin/env python3
"""Cross-architecture determinism check (roadmap item 06).

Asserts that the reference `backtester` binary produces a BYTE-IDENTICAL
deterministic metric block on aarch64 (built for aarch64-unknown-linux-gnu,
run under qemu-aarch64-static, or natively on an ARM runner) versus the
committed x86_64 goldens in data/golden/.

This is a stronger assertion than the cross-LANGUAGE parity harnesses
(parity_check / parity_regime / parity_forex), which compare Python vs Rust
at relative tolerance 1e-3. Here the SAME Rust source compiled for a
different CPU family must reproduce every printed metric digit exactly, so
the comparison is full string equality, not tolerance-bounded.

The metric block excludes wall-clock/load timing lines (non-deterministic);
only the `<tag> | Trades:.. ROI:.. PF:.. Shp:.. Win:.. Exp:.. MaxDD:..`
lines are compared. The determinism prerequisite (seeding the
indicator-variance perturbation, IND_VARIANCE_SEED=42) shipped in 26766d9.

Usage:
    # regenerate the x86_64 goldens from a native x86_64 binary:
    python tools/parity_arch.py --emit-golden --bin target/release/backtester

    # check an aarch64 binary under QEMU against the committed goldens:
    python tools/parity_arch.py \
        --bin target/aarch64-unknown-linux-gnu/release/backtester \
        --runner qemu-aarch64-static

    # check a native build (CI on an ARM runner: no runner prefix):
    python tools/parity_arch.py --bin target/release/backtester

Exit 0 = byte-identical on every dataset, 1 = a diff, 2 = setup failure.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = REPO / "data" / "golden"

DATASETS = [
    "SOLUSDT_1h",
    "BTCUSDT_30m",
    "DOGEUSDT_30m",
    "EURUSD_1h",
    "USDJPY_1h",
    "SYNTH_100k",
]

# Same metric-line shape parity_check.py's LINE_RE captures; wall-clock and
# load lines do not match and are therefore excluded.
METRIC_RE = re.compile(
    r"\|\s*Trades:\s*-?\d+\s+ROI:\s*\$?-?[\d,]+\.\d+R?\s+PF:"
)


def metric_block(binary: Path, csv: Path, runner: list[str]) -> str:
    cmd = runner + [str(binary), str(csv)]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0:
        sys.stderr.write(f"run failed ({' '.join(cmd)}):\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    return "".join(
        line + "\n" for line in proc.stdout.splitlines() if METRIC_RE.search(line)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=Path, required=True, help="backtester binary")
    ap.add_argument("--runner", action="append", default=[],
                    help="launcher prefix token (repeatable), e.g. qemu-aarch64-static")
    ap.add_argument("--emit-golden", action="store_true",
                    help="write data/golden/<ds>.x86_64.txt instead of checking")
    args = ap.parse_args()

    if not args.bin.exists():
        sys.stderr.write(f"binary not found: {args.bin}\n")
        return 2

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    n_bad = 0
    for ds in DATASETS:
        csv = REPO / "data" / f"{ds}.csv"
        if not csv.exists():
            print(f"  skip {ds}: dataset missing")
            continue
        block = metric_block(args.bin, csv, args.runner)
        golden = GOLDEN_DIR / f"{ds}.x86_64.txt"
        if args.emit_golden:
            golden.write_text(block)
            print(f"  wrote {golden.name} ({block.count(chr(10))} metric lines)")
            n_ok += 1
            continue
        if not golden.exists():
            print(f"  MISSING golden for {ds}: {golden}")
            n_bad += 1
            continue
        if block == golden.read_text():
            print(f"  OK   {ds}: byte-identical ({block.count(chr(10))} lines)")
            n_ok += 1
        else:
            n_bad += 1
            exp = golden.read_text().splitlines()
            got = block.splitlines()
            print(f"  DIFF {ds}: {len(exp)} golden vs {len(got)} produced lines")
            for i, (a, b) in enumerate(zip(exp, got)):
                if a != b:
                    print(f"      line {i}:\n        golden: {a}\n        got   : {b}")
                    break

    what = "emitted" if args.emit_golden else "checked"
    status = "OK" if n_bad == 0 else "FAIL"
    print(f"\nparity_arch: {n_ok} {what}, {n_bad} bad -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
