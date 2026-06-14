#!/usr/bin/env python3
"""Cross-language parity harness — *trade-ledger* level.

Where ``parity_check.py`` / ``parity_regime.py`` / ``parity_forex.py``
diff the seven scalar metrics each engine prints to stdout (trades,
ROI, PF, Sharpe, win rate, expectancy, max drawdown), this script
diffs the per-trade ``trade_list.csv`` row-by-row.

The motivation is a known gap in the metric-only diff: two engines
could agree on every reported scalar while taking different sets of
trades that happen to compensate in aggregate. Ledger parity catches
that — every trade's side, entry price, exit price, and PnL must agree
within tolerance.

Both engines write ``trade_list.csv`` to their own working directory
with identical schema:
    strategy, window, sample, side,
    entry_time, open_entry, high_entry, low_entry, close_entry,
    exit_time,  open_exit,  high_exit,  low_exit,  close_exit,
    pnl

Two engines write the timestamp columns in different formats:
  - Python: ISO-8601 ("2018-07-16 22:00:00")
  - Rust:   UNIX seconds (1531778400)
This script normalises both to UNIX seconds before comparison.

Usage:
    python tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001
"""
from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR",
                              REPO_RUST.parent / "quant-research-framework"))


@dataclass
class TradeRow:
    strategy: str
    window: str
    sample: str
    side: str
    entry_unix: int
    open_entry: float
    high_entry: float
    low_entry: float
    close_entry: float
    exit_unix: int
    open_exit: float
    high_exit: float
    low_exit: float
    close_exit: float
    pnl: float


def _parse_time(s: str) -> int:
    s = s.strip()
    # Rust writes integer seconds; Python writes ISO-8601 like
    # "2018-07-16 22:00:00" or "2018-07-16 22:00:00+00:00".
    if s.isdigit():
        return int(s)
    # ISO-8601 → UTC unix seconds. Strip any tz suffix; rows are UTC.
    import datetime as _dt
    if "+" in s:
        s = s.split("+", 1)[0].strip()
    dt = _dt.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    return int(dt.replace(tzinfo=_dt.timezone.utc).timestamp())


def _norm_side(s: str) -> str:
    """Engines disagree on side encoding: Python writes '1'/'-1',
    Rust writes 'long'/'short'. Normalise to '+1'/'-1' for diffing.
    This schema divergence is itself flagged in the paper §6.1 as a
    note: ledger fields are canonical only after normalisation."""
    s = s.strip()
    if s in ("1", "+1", "long", "+1.0"):
        return "+1"
    if s in ("-1", "short", "-1.0"):
        return "-1"
    return s  # let it fail downstream if unknown


def _row(d: dict) -> TradeRow:
    return TradeRow(
        strategy=d["strategy"],
        window=d["window"],
        sample=d["sample"],
        side=_norm_side(d["side"]),
        entry_unix=_parse_time(d["entry_time"]),
        open_entry=float(d["open_entry"]),
        high_entry=float(d["high_entry"]),
        low_entry=float(d["low_entry"]),
        close_entry=float(d["close_entry"]),
        exit_unix=_parse_time(d["exit_time"]),
        open_exit=float(d["open_exit"]),
        high_exit=float(d["high_exit"]),
        low_exit=float(d["low_exit"]),
        close_exit=float(d["close_exit"]),
        pnl=float(d["pnl"]),
    )


def load_ledger(path: Path) -> list[TradeRow]:
    with path.open() as f:
        return [_row(d) for d in csv.DictReader(f)]


def run_python(csv_path: Path) -> Path:
    """Run the Python engine and return the path to its written ledger.

    The engine's trade-ledger export is keyed to a single path (EXPORT_PATH) and
    is NOT safe for concurrent writers sharing one file: two engine processes
    pointed at the same trade_list.csv interleave and truncate each other's
    rows (observed: 3 concurrent runs -> 5,179 garbled rows instead of 1,726).
    We therefore give every invocation a private EXPORT_PATH under its own temp
    directory, so concurrent parity runs (or a parallel sweep) are isolated and
    each ledger is deterministic. This is also inherently clean — no stale file
    from a prior run can be appended to."""
    workdir = Path(tempfile.mkdtemp(prefix="qrf_py_ledger_"))
    out_path = workdir / "trade_list.csv"
    env = os.environ.copy()
    env["BT_CSV"] = str(Path(csv_path).resolve())
    env["MPLBACKEND"] = "Agg"
    driver = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO   = False
bt.EXPORT_PATH       = %r
bt.main()
""" % (str(REPO_PY), str(out_path))
    proc = subprocess.run([sys.executable, "-c", driver], env=env,
                          cwd=REPO_PY, capture_output=True, text=True,
                          timeout=900)
    if proc.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        sys.stderr.write(f"Python run failed:\n{proc.stderr}\n")
        sys.exit(2)
    return out_path


def ensure_rust_built() -> Path:
    """Build the Rust binary if missing or older than the engine source, and
    return its path. Rebuilding if stale (mirrors parity_check.py) prevents a
    stale binary — e.g. left by an ablation patch — from being diffed silently.
    Called up front in main() so neither engine run triggers a *mid-run*
    recompile, which (combined with a stale ledger) made this surface
    non-reproducible from a fresh checkout."""
    bin_path = REPO_RUST / "target" / "release" / "backtester"
    src = REPO_RUST / "src" / "lib.rs"
    stale = (not bin_path.exists()
             or (src.exists() and src.stat().st_mtime > bin_path.stat().st_mtime))
    if stale:
        subprocess.run(["cargo", "build", "--release"], cwd=REPO_RUST,
                       check=True, capture_output=True)
    return bin_path


def run_rust(csv_path: Path) -> Path:
    # Run in a private temp directory so the binary's cwd-relative trade_list.csv
    # can't collide with a concurrent run (see run_python). The binary takes the
    # CSV as an absolute argument, so it needs nothing else from its cwd.
    bin_path = ensure_rust_built()
    workdir = Path(tempfile.mkdtemp(prefix="qrf_rs_ledger_"))
    proc = subprocess.run([str(bin_path), str(Path(csv_path).resolve())],
                          cwd=workdir, capture_output=True, text=True,
                          timeout=600)
    if proc.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        sys.stderr.write(f"Rust run failed:\n{proc.stderr}\n")
        sys.exit(2)
    return workdir / "trade_list.csv"


def _norm_sample(s: str) -> str:
    """Engines use slightly different stage-label conventions. Map
    everything down to just IS or OOS for cross-engine matching;
    the per-stage breakdown is recoverable from the metric-level
    parity scripts."""
    s = s.strip().upper()
    if s.startswith("IS"):
        return "IS"
    if s.startswith("OOS"):
        return "OOS"
    return s


def _key(t: TradeRow) -> tuple:
    """Canonical cross-engine key: (sample bucket, entry_unix, side).
    Sample is normalised IS/OOS; strategy and window labels differ
    between engines and are intentionally excluded."""
    return (_norm_sample(t.sample), t.entry_unix, t.side)


_BASE_SAMPLES = {"IS", "OOS"}  # Python only writes these; Rust also writes
                                #  IS-opt/OOS-opt — exclude those for fairness.


def _dedupe(rows: list[TradeRow]) -> dict:
    """Both engines write the same trade across multiple stage labels.
    First filter to the base samples both engines emit (IS, OOS),
    then keep one representative per (sample, entry_unix, side) key.
    The IS-opt / OOS-opt extras Rust writes are stage-reporting
    duplicates of the same trade and are correctly checked at the
    metric-level by parity_check.py."""
    out: dict = {}
    for t in rows:
        if t.sample not in _BASE_SAMPLES:
            continue
        k = _key(t)
        out.setdefault(k, t)
    return out


def compare(py: list[TradeRow], rs: list[TradeRow], tol: float) -> int:
    """Return number of mismatches; 0 = parity ok."""
    py_by_key = _dedupe(py)
    rs_by_key = _dedupe(rs)

    print(f"Python ledger: {len(py):>5} raw rows; "
          f"{len(py_by_key)} unique trades after dedup by "
          f"(sample, entry_unix, side)")
    print(f"Rust   ledger: {len(rs):>5} raw rows; "
          f"{len(rs_by_key)} unique trades after dedup")

    py_keys = set(py_by_key)
    rs_keys = set(rs_by_key)
    only_py = py_keys - rs_keys
    only_rs = rs_keys - py_keys
    common  = py_keys & rs_keys

    fails = 0
    if only_py:
        print(f"\n[FAIL] {len(only_py)} trades in Python only "
              f"(first 5: {sorted(only_py)[:5]})")
        fails += len(only_py)
    if only_rs:
        print(f"[FAIL] {len(only_rs)} trades in Rust only "
              f"(first 5: {sorted(only_rs)[:5]})")
        fails += len(only_rs)

    field_fails = 0
    field_examples: dict[str, list] = {}
    for k in sorted(common):
        a, b = py_by_key[k], rs_by_key[k]
        for fld in ("open_entry", "close_entry", "open_exit",
                    "close_exit", "pnl"):
            av, bv = getattr(a, fld), getattr(b, fld)
            denom = max(abs(av), abs(bv), 1e-9)
            rel = abs(av - bv) / denom
            if rel > tol and not (abs(av) < 1e-6 and abs(bv) < 1e-6):
                field_fails += 1
                field_examples.setdefault(fld, []).append((k, av, bv, rel))

    if field_fails:
        print(f"\n[FAIL] {field_fails} field mismatches "
              f"({len(common)} common trades, {len(common) * 5} fields checked)")
        for fld, exs in field_examples.items():
            print(f"  {fld}: {len(exs)} mismatches; first 3:")
            for k, av, bv, rel in exs[:3]:
                print(f"    key={k} py={av} rs={bv} rel={rel:.2%}")
        fails += field_fails
    else:
        print(f"\n[OK] all {len(common) * 5} fields across {len(common)} "
              f"common trades agree within {tol}")

    return fails


def _full_coverage_check(py: list[TradeRow], rs: list[TradeRow],
                         tol: float) -> int:
    """Stronger coverage gate: the (sample, entry_unix, side) key used by
    compare() keeps one representative per key, so an overlapping WFO window
    that re-enters the same bar collapses to a single row. This check matches
    *all* base-stage rows on (sample, entry, exit, side) with multiplicity and
    compares the five fields under the same relative tolerance, so no
    economically-distinct trade can be silently dropped by the dedup. For a
    clean run this is 0; if it ever diverged while compare() agreed, the dedup
    would be masking a real difference."""
    from collections import defaultdict
    pf = [t for t in py if t.sample in _BASE_SAMPLES]
    rf = [t for t in rs if t.sample in _BASE_SAMPLES]

    def k4(t: TradeRow) -> tuple:
        return (_norm_sample(t.sample), t.entry_unix, t.exit_unix, t.side)

    pg: dict = defaultdict(list)
    rg: dict = defaultdict(list)
    for t in pf:
        pg[k4(t)].append(t)
    for t in rf:
        rg[k4(t)].append(t)

    fields = ("open_entry", "close_entry", "open_exit", "close_exit", "pnl")
    unmatched = 0
    field_fails = 0
    for k in set(pg) | set(rg):
        a = sorted(pg.get(k, []), key=lambda t: t.pnl)
        b = sorted(rg.get(k, []), key=lambda t: t.pnl)
        for x, y in zip(a, b):
            for fld in fields:
                av, bv = getattr(x, fld), getattr(y, fld)
                denom = max(abs(av), abs(bv), 1e-9)
                if abs(av - bv) / denom > tol and not (abs(av) < 1e-6
                                                       and abs(bv) < 1e-6):
                    field_fails += 1
        unmatched += abs(len(a) - len(b))

    total = unmatched + field_fails
    if total == 0:
        print(f"\n[OK] full base-ledger coverage: all {len(pf)} Python / "
              f"{len(rf)} Rust base-stage trades agree on (entry, exit, side) "
              f"— no trade masked by the entry-key dedup")
    else:
        print(f"\n[FAIL] full base-ledger coverage: {unmatched} unmatched + "
              f"{field_fails} field divergences over {len(pf)}/{len(rf)} base rows")
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True,
                    help="OHLC CSV path (relative or absolute)")
    ap.add_argument("--tol", type=float, default=1e-3,
                    help="relative tolerance for numeric fields (default 1e-3)")
    args = ap.parse_args()

    if not REPO_PY.exists():
        sys.stderr.write(f"Python repo not found at {REPO_PY}\n")
        return 2

    csv_abs = Path(args.csv).resolve()
    if not csv_abs.exists():
        sys.stderr.write(f"CSV not found: {csv_abs}\n")
        return 2

    print(f"Python repo: {REPO_PY}")
    print(f"Rust   repo: {REPO_RUST}")
    print(f"CSV        : {csv_abs}")
    print(f"Tolerance  : {args.tol*100:.3g}%\n")

    # Build the Rust binary up front so neither engine run triggers a mid-run
    # recompile; combined with each run writing its ledger to a private temp
    # directory (run_python/run_rust), this makes the surface reproducible from a
    # fresh checkout, run-to-run, and under concurrent invocation.
    ensure_rust_built()

    print("Running Python...")
    py_path = run_python(csv_abs)
    print("Running Rust...")
    rs_path = run_rust(csv_abs)

    py = load_ledger(py_path)
    rs = load_ledger(rs_path)
    shutil.rmtree(py_path.parent, ignore_errors=True)
    shutil.rmtree(rs_path.parent, ignore_errors=True)

    fails = compare(py, rs, args.tol)
    coverage = _full_coverage_check(py, rs, args.tol)
    if fails == 0 and coverage == 0:
        print("\nLEDGER PARITY OK")
        return 0
    # Report both surfaces so the summary line can't contradict the detail above
    # (a non-zero coverage with fails == 0 means the dedup masked a difference,
    # which is itself a failure — don't print "FAIL: 0 mismatches").
    print(f"\nLEDGER PARITY FAIL: {fails} keyed mismatches, "
          f"{coverage} full-coverage divergences")
    return 1


if __name__ == "__main__":
    sys.exit(main())
