#!/usr/bin/env python3
"""Cross-language parity harness: quant-research-framework (Python) vs
quant-research-framework-rs (Rust). Runs both engines on the same
synthetic CSV and compares the deterministic IS/OOS metrics that show up
in stdout.

Usage:
    python tools/parity_check.py                 # uses bundled synthetic
    python tools/parity_check.py --tol 0.02      # 2% relative tolerance
    python tools/parity_check.py --csv path.csv  # custom dataset

The Python repo is expected at ../quant-research-framework relative to
this Rust repo (or set QRF_PY_DIR). The Rust binary is built on demand
via `cargo build --release` from this repo's root.

Exit code 0 = within tolerance; 1 = mismatch; 2 = setup failure.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR",
                              REPO_RUST.parent / "quant-research-framework"))


@dataclass
class Metrics:
    trades: int | None = None
    roi: float | None = None
    pf: float | None = None
    sharpe: float | None = None
    win_rate: float | None = None
    exp: float | None = None
    max_dd: float | None = None

    def fields(self):
        return [("trades", self.trades), ("roi", self.roi), ("pf", self.pf),
                ("sharpe", self.sharpe), ("win_rate", self.win_rate),
                ("exp", self.exp), ("max_dd", self.max_dd)]


@dataclass
class Run:
    label: str
    metrics: dict[str, Metrics] = field(default_factory=dict)


# Both engines print lines that look like:
#   IS-raw      | Trades:  37  ROI:$2,468.62  PF:  1.10  Shp:  0.87  ...
# We capture the per-tag metrics by regex.
LINE_RE = re.compile(
    r"^\s*(?P<tag>[A-Za-z0-9_+\-:,]+(?:\s+[A-Za-z0-9_+\-:,]+)?)\s+"
    r"(?:\(LB[^)]*\)\s*)?\|\s*"
    r"Trades:\s*(?P<trades>\-?\d+)\s+"
    r"ROI:\s*\$?(?P<roi>\-?[\d,]+\.\d+)R?\s+"
    r"PF:\s*(?P<pf>\-?[\d.]+|inf)\s+"
    r"Shp:\s*(?P<shp>\-?[\d.]+)\s+"
    r"Win:\s*(?P<win>\-?[\d.]+)%\s+"
    r"Exp:\s*\$?(?P<exp>\-?[\d,]+\.\d+)R?\s+"
    r"MaxDD:\s*\$?(?P<dd>\-?[\d,]+\.\d+)R?",
)


def parse_metrics(stdout: str) -> dict[str, Metrics]:
    out: dict[str, Metrics] = {}
    for raw_line in stdout.splitlines():
        m = LINE_RE.match(raw_line)
        if not m:
            continue
        tag = m.group("tag").strip()
        out[tag] = Metrics(
            trades=int(m.group("trades")),
            roi=float(m.group("roi").replace(",", "")),
            pf=float("inf") if m.group("pf") == "inf" else float(m.group("pf")),
            sharpe=float(m.group("shp")),
            win_rate=float(m.group("win")) / 100.0,
            exp=float(m.group("exp").replace(",", "")),
            max_dd=float(m.group("dd").replace(",", "")),
        )
    return out


def generate_synth(out_path: Path, bars: int, seed: int) -> None:
    cmd = [sys.executable, "gen_synthetic.py",
           "--bars", str(bars), "--out", str(out_path), "--seed", str(seed)]
    subprocess.run(cmd, cwd=REPO_PY, check=True, capture_output=True)


def run_python(csv: Path) -> str:
    env = os.environ.copy()
    env["BT_CSV"] = str(csv)
    env["MPLBACKEND"] = "Agg"
    # Don't override the engine constants — the parity claim is "same
    # defaults produce the same numbers", so we only suppress the parts
    # both implementations agree are non-deterministic (Monte Carlo) or
    # interactive (matplotlib).
    driver = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO   = False
bt.main()
""" % str(REPO_PY)
    proc = subprocess.run([sys.executable, "-c", driver], env=env,
                          cwd=REPO_PY, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        sys.stderr.write(f"Python run failed:\n{proc.stderr}\n")
        sys.exit(2)
    return proc.stdout


def run_rust(csv: Path) -> str:
    bin_path = REPO_RUST / "target" / "release" / "backtester"
    if not bin_path.exists():
        subprocess.run(["cargo", "build", "--release"], cwd=REPO_RUST, check=True,
                       capture_output=True)
    proc = subprocess.run([str(bin_path), str(csv)], cwd=REPO_RUST,
                          capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr}\n")
        sys.exit(2)
    return proc.stdout


def compare(py: dict[str, Metrics], rs: dict[str, Metrics], tol: float) -> int:
    failures = 0
    interesting = ["IS-raw", "OOS-raw", "IS-opt", "OOS-opt",
                   "Baseline IS", "Baseline OOS", "W01 IS", "W01 OOS"]
    for tag in interesting:
        if tag not in py or tag not in rs:
            print(f"  [{tag}] missing in {'python' if tag not in py else 'rust'} output")
            failures += 1
            continue
        print(f"  [{tag}]")
        for fname, fpy in py[tag].fields():
            frs = getattr(rs[tag], fname)
            if fpy is None or frs is None:
                continue
            if fname == "trades":
                ok = fpy == frs
                marker = "OK" if ok else "MISMATCH"
                print(f"    {fname:>8}: py={fpy}  rs={frs}  [{marker}]")
                if not ok: failures += 1
                continue
            denom = max(abs(fpy), abs(frs), 1e-9)
            rel = abs(fpy - frs) / denom
            ok = rel <= tol or (abs(fpy) < 1e-6 and abs(frs) < 1e-6)
            marker = "OK" if ok else "MISMATCH"
            print(f"    {fname:>8}: py={fpy:>12.4f}  rs={frs:>12.4f}  rel={rel:6.2%}  [{marker}]")
            if not ok: failures += 1
    return failures


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=None,
                   help="OHLC CSV to use (default: REPO_RUST/data/SOLUSDT_1h.csv if present, else generate)")
    p.add_argument("--bars", type=int, default=48_000,
                   help="bars to generate when no CSV is supplied (default: 48000)")
    p.add_argument("--seed", type=int, default=20260425)
    p.add_argument("--tol", type=float, default=0.05,
                   help="relative tolerance for metric comparison (default 5%%)")
    args = p.parse_args()

    if not REPO_PY.exists():
        sys.stderr.write(f"Python repo not found at {REPO_PY}\n"
                         f"Set QRF_PY_DIR or check out the sibling repo there.\n")
        return 2

    csv = args.csv
    if csv is None:
        bundled = REPO_RUST / "data" / "SOLUSDT_1h.csv"
        if bundled.exists():
            csv = bundled
            print(f"Using bundled dataset: {csv}")
        else:
            csv = REPO_PY / "data" / "PARITY_SYNTH.csv"
            print(f"Generating {args.bars} bars to {csv} (seed={args.seed})")
            generate_synth(csv, args.bars, args.seed)

    print(f"Python repo: {REPO_PY}")
    print(f"Rust  repo: {REPO_RUST}")
    print(f"CSV       : {csv}")
    print(f"Tolerance : {args.tol*100:.1f}%\n")

    print("Running Python...")
    py_out = run_python(csv)
    print("Running Rust...")
    rs_out = run_rust(csv)

    py_metrics = parse_metrics(py_out)
    rs_metrics = parse_metrics(rs_out)
    print(f"\nParsed: python={len(py_metrics)} tagged lines, rust={len(rs_metrics)} tagged lines")

    print("\nComparing baseline IS-raw / OOS-raw:")
    failures = compare(py_metrics, rs_metrics, args.tol)
    if failures == 0:
        print("\n  PARITY OK")
        return 0
    print(f"\n  PARITY FAIL: {failures} mismatches outside ±{args.tol*100:.0f}%")
    return 1


if __name__ == "__main__":
    sys.exit(main())
