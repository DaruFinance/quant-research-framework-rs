#!/usr/bin/env python3
"""Cross-language parity harness for the Phase 3 T2 pairs pipeline.

Compares the Python `backtester.pairs.{spread,eligibility,screener}`
primitives against the Rust `quant_research_framework_rs::pairs` port
on the DS-PAIR-BTCETH fixture (1500 1h bars).

Cross-language tolerances:
  * `ols_slope_intercept`, `kalman_beta_spread`, `ols_resid`,
    `half_life_ou`, `distance_ssd` — closed-form OLS / deterministic
    float math => 1e-9.
  * `engle_granger` ADF tau — matches Python `adfuller(resid,
    regression='n', maxlag=0, autolag=None)` to f64 noise (~1e-12
    in practice).  The production Python `engle_granger` uses
    statsmodels' default autolag='AIC' (lag selected adaptively),
    which would not be cross-impl-stable; this parity script pins
    Python to maxlag=0 explicitly so the comparison is meaningful.

Single-threaded by user request: cargo runs with --jobs 1, no
multiprocessing on the Python side.

Usage:
    python tools/parity_pairs.py            # default tolerance
    python tools/parity_pairs.py --tol 0.001

Exit 0 = parity OK, 1 = mismatch.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get(
    "QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))


RUST_DRIVER = r'''
//! Cross-language parity harness binary (Phase 3 T2 pairs).
//! Generated at runtime by tools/parity_pairs.py.

#![cfg(feature = "pairs")]

use std::path::PathBuf;

use quant_research_framework_rs::pairs::{
    distance_ssd, engle_granger, half_life_ou,
    kalman_beta_spread, log_ratio, ols_resid,
};
use quant_research_framework_rs::panel::{load_panel, PanelData};

fn build_panel(base: &str) -> PanelData {
    let assets: Vec<(String, PathBuf)> = vec![
        ("BTC".to_string(), format!("{}/BTC.csv", base).into()),
        ("ETH".to_string(), format!("{}/ETH.csv", base).into()),
    ];
    load_panel(&assets).expect("load_panel failed")
}

fn close_at(panel: &PanelData, asset: &str) -> Vec<f64> {
    let close_idx = panel.fields.iter().position(|f| f == "close").unwrap();
    let ai = panel.assets.iter().position(|a| a == asset).unwrap();
    (0..panel.times.len())
        .map(|t| panel.data[[t, ai, close_idx]])
        .collect()
}

fn main() {
    let base = std::env::args().nth(1).unwrap_or_else(|| {
        "tests/fixtures/sources".to_string()
    });
    let panel = build_panel(&base);
    let t_idx: usize = std::env::args()
        .nth(2)
        .and_then(|s| s.parse().ok())
        .unwrap_or(1000);

    // Bare engle_granger over a fixed window.
    let lookback = 500usize;
    let close_a = close_at(&panel, "BTC");
    let close_b = close_at(&panel, "ETH");
    let s = t_idx + 1 - lookback;
    let e = t_idx + 1;
    let (tau, beta) = engle_granger(&close_a[s..e], &close_b[s..e]).unwrap();
    println!("engle_granger_tau={:.12}", tau);
    println!("engle_granger_beta={:.12}", beta);

    // distance_ssd over the same window.
    let d = distance_ssd(&close_a[s..e], &close_b[s..e]).unwrap();
    println!("distance_ssd={:.12}", d);

    // ols_resid: report the spread at five evenly-spaced bars.
    let resid = ols_resid(&panel, "BTC", "ETH", t_idx, 60).unwrap();
    let probes = [200usize, 400, 600, 800, 1000];
    for (i, p) in probes.iter().enumerate() {
        println!("ols_resid_spread_{}={:.12}", i, resid.spread[*p]);
    }
    println!("ols_resid_beta_final={:.12}", resid.beta_scalar.unwrap());

    // half_life_ou over the resid up to t_idx.
    let resid_clean: Vec<f64> = resid.spread[59..=t_idx]
        .iter()
        .copied()
        .collect();
    let hl = half_life_ou(&resid_clean);
    // half_life can be +inf for non-mean-reverting; emit as-is.
    println!("half_life_ou={:.12}", if hl.is_finite() { hl } else { -1.0 });

    // kalman_beta_spread: report spread + beta at the same five probes.
    let kalman = kalman_beta_spread(&panel, "BTC", "ETH", t_idx, 1e-4, 1e-3).unwrap();
    let beta_traj = kalman.beta_traj.unwrap();
    for (i, p) in probes.iter().enumerate() {
        println!("kalman_spread_{}={:.12}", i, kalman.spread[*p]);
        println!("kalman_beta_{}={:.12}", i, beta_traj[*p]);
    }

    // log_ratio at the probes (sanity check — should be exact).
    let lr = log_ratio(&panel, "BTC", "ETH", t_idx).unwrap();
    for (i, p) in probes.iter().enumerate() {
        println!("log_ratio_{}={:.12}", i, lr.spread[*p]);
    }
}
'''


def write_rust_fixtures(base_dir: Path) -> None:
    """Convert the Python pair-panel parquet into BTC.csv/ETH.csv so
    the Rust panel loader can read it."""
    import pandas as pd
    fix = REPO_PY / "tests" / "fixtures" / "pair_btc_eth_1h_1500.parquet"
    df = pd.read_parquet(fix)
    # Schema: long-form (time, asset, open, high, low, close[, volume]).
    # The fixture uses BTC and ETH; some builds emit a wide-form
    # frame (`btc_close`, `eth_close`).  Handle both.
    if "asset" in df.columns:
        for asset in ("BTC", "ETH"):
            sub = df[df["asset"] == asset].sort_values("time").reset_index(drop=True)
            cols = ["time", "open", "high", "low", "close"]
            if "volume" in sub.columns:
                cols.append("volume")
            out = sub[cols].copy()
            out["time"] = out["time"].astype("int64")
            out.to_csv(base_dir / f"{asset}.csv", index=False)
        return
    # Wide form fallback.
    for asset in ("BTC", "ETH"):
        prefix = asset.lower()
        cols_map = {f"{prefix}_open": "open", f"{prefix}_high": "high",
                     f"{prefix}_low": "low", f"{prefix}_close": "close"}
        if f"{prefix}_volume" in df.columns:
            cols_map[f"{prefix}_volume"] = "volume"
        sub = df[["time", *cols_map.keys()]].rename(columns=cols_map)
        sub["time"] = sub["time"].astype("int64")
        sub.to_csv(base_dir / f"{asset}.csv", index=False)


def run_rust(base_dir: Path, t_idx: int) -> Dict[str, float]:
    src = REPO_RUST / "examples" / "_parity_pairs.rs"
    src.write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release",
         "--features", "pairs", "--example", "_parity_pairs"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_pairs"
    proc = subprocess.run(
        [str(bin_path), str(base_dir), str(t_idx)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, float] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        try:
            out[key] = float(val)
        except ValueError:
            pass
    return out


def run_python(base_dir: Path, t_idx: int) -> Dict[str, float]:
    """Run the Python primitives via a subprocess (clean environment).
    The subprocess loads the Rust-side CSVs into a panel via
    `backtester.panel.load_panel`, then calls the same primitives the
    Rust binary did.  Engle-Granger ADF runs with maxlag=0 explicitly
    so the cross-language tau is bit-comparable."""
    driver = f'''
import sys
sys.path.insert(0, {str(REPO_PY)!r})

import numpy as np
from statsmodels.tsa.stattools import adfuller

from backtester.panel import load_panel
from backtester.pairs.spread import (
    log_ratio, ols_resid, kalman_beta_spread,
)
from backtester.pairs.eligibility import half_life_ou
from backtester.pairs.screener import distance_ssd

base = {str(base_dir)!r}
panel = load_panel({{
    "BTC": f"{{base}}/BTC.csv",
    "ETH": f"{{base}}/ETH.csv",
}})

t_idx = {t_idx}
lookback = 500
close = panel.ds["close"].values
ai_a = panel.assets.index("BTC")
ai_b = panel.assets.index("ETH")

a_window = close[t_idx + 1 - lookback : t_idx + 1, ai_a]
b_window = close[t_idx + 1 - lookback : t_idx + 1, ai_b]

# engle_granger replicated with maxlag=0, autolag=None for parity.
log_a = np.log(a_window); log_b = np.log(b_window)
beta, alpha = np.polyfit(log_b, log_a, 1)
resid = log_a - alpha - beta * log_b
adf = adfuller(resid, regression="n", maxlag=0, autolag=None)
print(f"engle_granger_tau={{adf[0]:.12f}}")
print(f"engle_granger_beta={{beta:.12f}}")
print(f"distance_ssd={{distance_ssd(a_window, b_window):.12f}}")

resid_full = ols_resid(panel, "BTC", "ETH", t_idx, lookback=60)
probes = [200, 400, 600, 800, 1000]
for i, p in enumerate(probes):
    print(f"ols_resid_spread_{{i}}={{resid_full.spread[p]:.12f}}")
print(f"ols_resid_beta_final={{resid_full.beta:.12f}}")

resid_clean = resid_full.spread[59:t_idx + 1]
resid_clean = resid_clean[~np.isnan(resid_clean)]
hl = half_life_ou(resid_clean)
print(f"half_life_ou={{(hl if np.isfinite(hl) else -1.0):.12f}}")

kalman = kalman_beta_spread(panel, "BTC", "ETH", t_idx,
                              delta=1e-4, observation_var=1e-3)
for i, p in enumerate(probes):
    print(f"kalman_spread_{{i}}={{kalman.spread[p]:.12f}}")
    print(f"kalman_beta_{{i}}={{kalman.beta[p]:.12f}}")

lr = log_ratio(panel, "BTC", "ETH", t_idx)
for i, p in enumerate(probes):
    print(f"log_ratio_{{i}}={{lr.spread[p]:.12f}}")
'''
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Python run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, float] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        try:
            out[key] = float(val)
        except ValueError:
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tol", type=float, default=1e-3,
                          help="default relative tolerance for closed-form values")
    parser.add_argument("--t-idx", type=int, default=1000,
                          help="bar index to evaluate at")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="qrf_parity_pairs_") as tmp:
        base_dir = Path(tmp)
        write_rust_fixtures(base_dir)
        rs = run_rust(base_dir, args.t_idx)
        py = run_python(base_dir, args.t_idx)

    keys = sorted(set(rs.keys()) | set(py.keys()))
    failures: List[str] = []
    print(f"Tolerance: {args.tol:.0e} (closed-form 1e-9 inside)")
    for k in keys:
        if k not in rs or k not in py:
            print(f"  {k}: MISSING (py={k in py}, rs={k in rs})")
            failures.append(k)
            continue
        rv, pv = rs[k], py[k]
        # Closed-form keys get the tighter 1e-9 tolerance regardless.
        tight = k.startswith(("log_ratio", "kalman_", "ols_resid_",
                                "engle_granger_", "distance_ssd",
                                "half_life_ou"))
        tol = 1e-9 if tight else args.tol
        if rv == pv:
            print(f"  {k}: py={pv:.12f}  rs={rv:.12f}  EXACT")
            continue
        denom = max(abs(pv), abs(rv), 1e-15)
        rel = abs(pv - rv) / denom
        if rel <= tol:
            print(f"  {k}: py={pv:.12f}  rs={rv:.12f}  rel={rel:.2e}  [OK]")
        else:
            print(f"  {k}: py={pv:.12f}  rs={rv:.12f}  rel={rel:.2e}  [FAIL]")
            failures.append(k)

    if failures:
        print(f"\nPAIRS PARITY FAILED: {len(failures)} mismatch(es)")
        return 1
    print("\nPAIRS PARITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
