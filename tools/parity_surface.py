#!/usr/bin/env python3
"""Cross-language parity harness for the IS parameter-robustness isosurface.

Proves Python and Rust dense IS objective grids agree cell-by-cell
within rel-tol 1e-3 + abs floor on seeded fixtures.

Mirrors tools/parity_dsr.py: write fixture -> Python emit -> generate
examples/_parity_surface.rs (gitignored) -> cargo build --jobs 1 --release
--example _parity_surface -> Rust emit -> compare grids keyed on
(window_idx, regime, lb, rrr, sl_idx). The float `sl` is compared as a VALUE
under tolerance (not a key) to avoid cross-engine float-format key drift.

The example is a plain [[example]] (NO required-features): the emit is pure
CSV/f64 with zero new crates, unlike _parity_dsr (which needs statrs via `dsr`).
Forces CSV on both sides (no Rust parquet reader). Single-threaded.

Fixtures:
  * uniform crypto (default)        — base case
  * irregular-spacing crypto (--irregular) — exercises bar-Sharpe ppy parity
The Sharpe convention is pinned on BOTH engines from --sharpe {trade,bar}.

Usage:
    python tools/parity_surface.py
    python tools/parity_surface.py --sl3                 # 3-axis SL sweep (crypto)
    python tools/parity_surface.py --sharpe bar          # pin bar-Sharpe both sides
    python tools/parity_surface.py --irregular           # irregular timestamps

Exit 0 = parity OK, 1 = mismatch, 2 = build/run error.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Tuple

import numpy as np

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))

_METRICS = ("roi", "pf", "sharpe", "mdd")
_KEY = ("window_idx", "regime", "lb", "rrr", "sl_idx")


def write_fixture(path: Path, n: int = 4000, seed: int = 20260611,
                  irregular: bool = False) -> None:
    """Seeded OHLC fixture (5-col schema both loaders expect). When `irregular`,
    timestamps have gaps so bar-Sharpe's median-spacing ppy is exercised across
    engines (uniform spacing would mask a divergence)."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, n)))
    t = 1_600_000_000
    lines = ["time,open,high,low,close"]
    prev = close[0]
    for i in range(n):
        c = close[i]
        o = prev
        hi = max(o, c) * (1.0 + abs(rng.normal(0.0, 0.002)))
        lo = min(o, c) * (1.0 - abs(rng.normal(0.0, 0.002)))
        lines.append(f"{t},{o:.6f},{hi:.6f},{lo:.6f},{c:.6f}")
        step = 3600
        if irregular and (i % 7 == 0):
            step = 3600 * 3          # occasional gap
        t += step
        prev = c
    path.write_text("\n".join(lines) + "\n")


def run_python(fixture: Path, workdir: Path, sl3: bool, sharpe: str) -> Dict[Tuple, Dict[str, float]]:
    sys.path.insert(0, str(REPO_PY))
    import backtester as bt  # noqa: E402
    from backtester import opt_surface as osf  # noqa: E402

    df = bt.load_ohlc(str(fixture))
    bt.__dict__["EMIT_OPT_SURFACE"] = True
    bt.__dict__["EMIT_OPT_SURFACE_SL"] = sl3
    bt.__dict__["SHARPE_MODE"] = sharpe          # pin convention
    os.environ["EMIT_OPT_SURFACE_FMT"] = "csv"
    bt.__dict__["EXPORT_PATH"] = str(workdir / "trade_list.csv")
    osf.emit_surface_classic(bt, df, window_idx="0", write_header=True)
    return _read_grid(workdir / "opt_surface.csv")


RUST_DRIVER = r'''//! Cross-language parity harness binary (item #1 — IS surface).
//! Generated at runtime by tools/parity_surface.py. Emits the dense classic IS
//! objective grid for the whole fixture as window_idx "0".
use quant_research_framework_rs::{load_ohlc, Bar, Config, compute_ema};
use quant_research_framework_rs::opt_surface::emit_surface_classic;

fn ema_crossover(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let ema_fast = compute_ema(&close, 20);
    let ema_slow = compute_ema(&close, lb);
    let n = bars.len();
    let mut raw = vec![0i8; n];
    for i in 1..n {
        if ema_fast[i - 1] > ema_slow[i - 1] { raw[i] = 1; }
        else if ema_fast[i - 1] < ema_slow[i - 1] { raw[i] = -1; }
    }
    raw
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let fixture = &args[1];
    let sl3 = args.get(2).map(|s| s == "1").unwrap_or(false);
    let sharpe_bar = args.get(3).map(|s| s == "bar").unwrap_or(false);
    let bars = load_ohlc(fixture);
    let mut cfg = Config::new();
    cfg.emit_opt_surface = true;
    cfg.emit_opt_surface_sl = sl3;
    cfg.sharpe_bar = sharpe_bar;                 // pin convention to match Python
    emit_surface_classic(&bars, "0", &cfg, ema_crossover, true);
}
'''


def run_rust(fixture: Path, workdir: Path, sl3: bool, sharpe: str) -> Dict[Tuple, Dict[str, float]]:
    (REPO_RUST / "examples" / "_parity_surface.rs").write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release", "--example", "_parity_surface"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900)
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-3000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_surface"
    proc = subprocess.run(
        [str(bin_path), str(fixture), "1" if sl3 else "0", sharpe],
        cwd=workdir, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-3000:]}\n")
        sys.exit(2)
    return _read_grid(workdir / "opt_surface.csv")


def _parse_float(tok: str) -> float:
    t = tok.strip()
    if t in ("nan", ""):
        return float("nan")
    if t in ("inf", "+inf"):
        return float("inf")
    if t == "-inf":
        return float("-inf")
    return float(t)


def _read_grid(path: Path) -> Dict[Tuple, Dict[str, float]]:
    out: Dict[Tuple, Dict[str, float]] = {}
    lines = path.read_text().splitlines()
    idx = {name: i for i, name in enumerate(lines[0].split(","))}
    for line in lines[1:]:
        if not line.strip():
            continue
        f = line.split(",")
        key = (f[idx["window_idx"]], f[idx["regime"]], int(f[idx["lb"]]),
               int(f[idx["rrr"]]), int(f[idx["sl_idx"]]))
        out[key] = {m: _parse_float(f[idx[m]]) for m in _METRICS}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-3, help="relative tolerance")
    ap.add_argument("--atol", type=float, default=1e-9, help="absolute floor")
    ap.add_argument("--sl3", action="store_true", help="3-axis SL sweep (crypto)")
    ap.add_argument("--sharpe", choices=("trade", "bar"), default="trade")
    ap.add_argument("--irregular", action="store_true",
                    help="irregular-spacing fixture (bar-Sharpe ppy parity)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        fixture = td / "surface_fixture.csv"
        write_fixture(fixture, irregular=args.irregular)
        pdir = td / "py"; pdir.mkdir()
        rdir = td / "rust"; rdir.mkdir()
        py = run_python(fixture, pdir, args.sl3, args.sharpe)
        rs = run_rust(fixture, rdir, args.sl3, args.sharpe)

    keys = sorted(set(py) | set(rs))
    n_ok = n_bad = 0
    for k in keys:
        if k not in py or k not in rs:
            print(f"  MISSING {k}: py={k in py} rs={k in rs}")
            n_bad += 1
            continue
        for m in _METRICS:
            a, b = py[k][m], rs[k][m]
            if not (math.isfinite(a) and math.isfinite(b)):
                if (math.isnan(a) == math.isnan(b)) and (math.isinf(a) == math.isinf(b)) \
                   and (math.copysign(1, a) == math.copysign(1, b) if math.isinf(a) else True):
                    n_ok += 1
                else:
                    print(f"  DIFF {k}.{m}: py={a!r} rs={b!r} (non-finite mismatch)")
                    n_bad += 1
                continue
            abs_diff = abs(a - b)
            rel = abs_diff / max(abs(a), abs(b), 1e-12)
            if rel <= args.tol or abs_diff <= args.atol:
                n_ok += 1
            else:
                print(f"  DIFF {k}.{m}: py={a:.12g} rs={b:.12g} "
                      f"rel={rel:.3e} abs={abs_diff:.3e} > tol={args.tol:.0e}")
                n_bad += 1

    status = "OK" if n_bad == 0 else "FAIL"
    print(f"parity_surface[sharpe={args.sharpe},sl3={args.sl3},"
          f"irregular={args.irregular}]: {n_ok}/{n_ok + n_bad} points within "
          f"tol={args.tol:.0e} ({len(keys)} cells) -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
