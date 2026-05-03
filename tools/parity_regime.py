#!/usr/bin/env python3
"""Focused regime-path parity check: USE_REGIME_SEG + USE_WFO at otherwise-default
settings. Isolates the regime+WFO code path from forex/session interactions
that `parity_combo.py` mixes in.

Usage:
    python tools/parity_regime.py
    python tools/parity_regime.py --tol 0.001    # strict
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR",
                              REPO_RUST.parent / "quant-research-framework"))

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


def parse_metrics(stdout: str) -> dict[str, dict]:
    out = {}
    for raw_line in stdout.splitlines():
        m = LINE_RE.match(raw_line)
        if not m: continue
        out[m.group("tag").strip()] = {
            "trades":   int(m.group("trades")),
            "roi":      float(m.group("roi").replace(",", "")),
            "pf":       float("inf") if m.group("pf") == "inf" else float(m.group("pf")),
            "sharpe":   float(m.group("shp")),
            "win_rate": float(m.group("win")) / 100.0,
            "exp":      float(m.group("exp").replace(",", "")),
            "max_dd":   float(m.group("dd").replace(",", "")),
        }
    return out


def run_python(csv: Path) -> str:
    env = os.environ.copy()
    env["BT_CSV"] = str(Path(csv).resolve()); env["MPLBACKEND"] = "Agg"
    driver = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO   = False
bt.USE_WFO           = True
bt.USE_REGIME_SEG    = True
bt.main()
""" % str(REPO_PY)
    proc = subprocess.run([sys.executable, "-c", driver], env=env,
                          cwd=REPO_PY, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        sys.stderr.write(f"Python failed:\n{proc.stderr[-2000:]}\n"); sys.exit(2)
    return proc.stdout


RUST_DRIVER = """
use quant_research_framework_rs::{
    Bar, Config, RegimeConfig, compute_ema, load_ohlc,
};

fn ema_strategy(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = compute_ema(&close, 20);
    let slow = compute_ema(&close, lb);
    let n = bars.len();
    let mut raw = vec![0i8; n];
    for i in 1..n {
        if fast[i - 1].is_nan() || slow[i - 1].is_nan() { continue; }
        raw[i] = if fast[i - 1] > slow[i - 1] { 1 } else { -1 };
    }
    raw
}

fn main() {
    let csv = std::env::args().nth(1).unwrap_or_else(|| "data/SOLUSDT_1h.csv".into());
    let bars = load_ohlc(&csv);
    println!("Loaded {} bars from {}", bars.len(), csv);
    let cfg = Config::new();           // all defaults
    let regime_cfg = RegimeConfig::default();
    quant_research_framework_rs::run_with_regime_cfg(
        &bars, "Regime", ema_strategy, regime_cfg, cfg);
}
"""


def run_rust(csv: Path) -> str:
    src = REPO_RUST / "examples" / "_parity_regime.rs"
    src.write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--release", "--example", "_parity_regime"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n"); sys.exit(2)
    proc = subprocess.run(
        ["cargo", "run", "--release", "--example", "_parity_regime", "--", str(csv)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        sys.stderr.write(f"Rust failed:\n{proc.stderr[-2000:]}\n"); sys.exit(2)
    return proc.stdout


def report(py: dict, rs: dict, tags: list[str], tol: float) -> int:
    diffs = 0
    print(f"\nMetric comparison ({len(tags)} tags, tol={tol*100:.1f}%):\n")
    for tag in tags:
        if tag not in py and tag not in rs:
            continue
        if tag not in py:
            print(f"  [{tag}]  rust-only:  {rs[tag]}"); diffs += 1; continue
        if tag not in rs:
            print(f"  [{tag}]  py-only:    {py[tag]}"); diffs += 1; continue
        print(f"  [{tag}]")
        for k in ("trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"):
            p = py[tag][k]; r = rs[tag][k]
            if k == "trades":
                ok = "OK" if p == r else "DIFF"
                if ok == "DIFF": diffs += 1
                print(f"    {k:>8}: py={p}  rs={r}  [{ok}]")
                continue
            denom = max(abs(p), abs(r), 1e-9)
            rel = abs(p - r) / denom
            ok = "OK" if (rel <= tol or (abs(p) < 1e-6 and abs(r) < 1e-6)) else "DIFF"
            if ok == "DIFF": diffs += 1
            print(f"    {k:>8}: py={p:>14.4f}  rs={r:>14.4f}  rel={rel:6.2%}  [{ok}]")
    print(f"\n{'PARITY OK' if diffs == 0 else f'PARITY DIFF: {diffs} mismatched fields'}")
    return diffs


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tol", type=float, default=0.001)
    p.add_argument("--csv", type=Path, default=REPO_RUST / "data" / "SOLUSDT_1h.csv")
    args = p.parse_args()
    if not args.csv.exists():
        print(f"need {args.csv}"); return 2
    print(f"CSV: {args.csv}")
    print("Flags: USE_REGIME_SEG=True, USE_WFO=True, all other flags default")
    print("       (USE_MONTE_CARLO=False to keep it deterministic)\n")
    print("Running Python regime+WFO...")
    py = parse_metrics(run_python(args.csv))
    print(f"  parsed {len(py)} tagged lines")
    print("Running Rust   regime+WFO...")
    rs = parse_metrics(run_rust(args.csv))
    print(f"  parsed {len(rs)} tagged lines")

    tags = ["IS-raw", "OOS-raw", "IS-opt", "OOS-opt",
            "Baseline IS", "Baseline OOS",
            "W01 IS", "W01 OOS", "W02 IS", "W02 OOS",
            "W03 IS", "W03 OOS", "W04 IS", "W04 OOS"]
    diffs = report(py, rs, tags, args.tol)
    return 0 if diffs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
