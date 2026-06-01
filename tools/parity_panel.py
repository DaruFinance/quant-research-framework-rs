#!/usr/bin/env python3
"""Cross-language parity harness for the Phase 2 panel pipeline.

Runs every panel primitive (equal_weights, ERC, neutralizations,
basket positions, constraints, Sortino/turnover, multi-term score)
in Python and in Rust on the same fixture (DS-PANEL-3 at t=500), and
diffs the outputs at a tolerance appropriate for each (closed-form
math at 1e-9, iterative ERC at 1e-3 relative — different solvers
across the language boundary).

Usage:
    python tools/parity_panel.py            # default tolerance
    python tools/parity_panel.py --tol 0.001

Exit 0 = parity OK, 1 = mismatch.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR",
                               REPO_RUST.parent / "quant-research-framework"))
SRC_FIXTURES = REPO_RUST / "tests" / "fixtures" / "sources"


RUST_DRIVER = r'''
//! Cross-language parity harness binary (Phase 2 catchup).
//! Generated at runtime by tools/parity_panel.py; matches the
//! `_parity_*.rs` gitignore pattern used by parity_regime.py and
//! parity_forex.py.

#![cfg(feature = "panel")]

use std::path::PathBuf;

use quant_research_framework_rs::metrics::{sortino, turnover};
use quant_research_framework_rs::objectives::MultiTermObjective;
use quant_research_framework_rs::panel::{
    apply_constraints, equal_weights, erc_weights,
    estimate_betas, estimate_vols, load_panel,
    LongShortBasket, momentum_alpha,
    sizing::cov_from_returns,
    neutralize::{neutralize_beta, neutralize_dollar, neutralize_sigma},
    strategies::long_short::NeutralizeMode,
};

fn fixture_paths(base: &str) -> Vec<(String, PathBuf)> {
    vec![
        ("SOL".to_string(), format!("{}/SOLUSDT_1h_30000_31000.csv", base).into()),
        ("BTC".to_string(), format!("{}/BTCUSDT_1h_jan_feb_2024.csv", base).into()),
        ("ETH".to_string(), format!("{}/ETHUSDT_1h_jan_feb_2024.csv", base).into()),
    ]
}

fn returns_window(panel: &quant_research_framework_rs::panel::PanelData,
                  t_end: usize, lookback: usize) -> Vec<Vec<f64>> {
    let close_idx = panel.fields.iter().position(|f| f == "close").unwrap();
    let n_assets = panel.assets.len();
    let start = t_end - lookback - 1;
    let mut prev: Vec<f64> = (0..n_assets)
        .map(|ai| panel.data[[start, ai, close_idx]])
        .collect();
    let mut window = Vec::with_capacity(lookback);
    for ti in (start + 1)..t_end {
        let mut row = Vec::with_capacity(n_assets);
        for ai in 0..n_assets {
            let c = panel.data[[ti, ai, close_idx]];
            row.push((c / prev[ai]).ln());
            prev[ai] = c;
        }
        window.push(row);
    }
    window
}

fn main() {
    let base = std::env::args().nth(1).unwrap_or_else(|| {
        "tests/fixtures/sources".to_string()
    });
    let panel = load_panel(&fixture_paths(&base)).expect("load_panel failed");

    let eq = equal_weights(panel.assets.len()).unwrap();
    println!("eq_weights={}", eq.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));

    let window = returns_window(&panel, 500, 100);
    let cov = cov_from_returns(&window).unwrap();
    let erc = erc_weights(&cov).unwrap();
    println!("erc_weights={}", erc.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));

    let raw = vec![1.0, -1.0, 1.0];
    let btc_idx = panel.assets.iter().position(|a| a == "BTC").unwrap();
    let betas = estimate_betas(&window, btc_idx).unwrap();
    let vols = estimate_vols(&window).unwrap();
    println!("betas={}", betas.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));
    println!("vols={}", vols.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));

    let w_dollar = neutralize_dollar(&raw).unwrap();
    println!("neutralize_dollar={}", w_dollar.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));
    let w_beta = neutralize_beta(&raw, &betas, Some(btc_idx)).unwrap();
    println!("neutralize_beta={}", w_beta.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));
    let w_sigma = neutralize_sigma(&raw, &vols).unwrap();
    println!("neutralize_sigma={}", w_sigma.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));

    let basket = LongShortBasket::new(momentum_alpha(20), NeutralizeMode::Dollar, 1, 1);
    let positions = basket.positions(&panel, 500).unwrap();
    let mut out_positions = String::new();
    for asset in &panel.assets {
        if !out_positions.is_empty() { out_positions.push(','); }
        out_positions.push_str(&format!("{:.12}", positions[asset]));
    }
    println!("basket_dollar={}", out_positions);

    let capped = apply_constraints(&[0.6, 0.2, 0.2], Some(0.5), None).unwrap();
    println!("constraints_cap_05_06_02_02={}", capped.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));
    let dropped = apply_constraints(&[0.5, 0.3, 0.2], Some(0.3), None).unwrap();
    println!("constraints_cap_03_05_03_02={}", dropped.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));
    let gross_scaled = apply_constraints(&[2.0, -1.0, 1.5], None, Some(3.0)).unwrap();
    println!("constraints_gross_3={}", gross_scaled.iter().map(|x| format!("{:.12}", x)).collect::<Vec<_>>().join(","));

    let close_idx = panel.fields.iter().position(|f| f == "close").unwrap();
    let sol_idx = panel.assets.iter().position(|a| a == "SOL").unwrap();
    let mut sol_rets = Vec::new();
    for ti in 1..panel.times.len() {
        let c0 = panel.data[[ti - 1, sol_idx, close_idx]];
        let c1 = panel.data[[ti, sol_idx, close_idx]];
        sol_rets.push((c1 / c0).ln());
    }
    println!("sortino_sol={:.12}", sortino(&sol_rets, None));
    println!("sortino_sol_ann252={:.12}", sortino(&sol_rets, Some(252.0)));

    let positions = vec![0.5, -0.5, 0.5, 0.0, 0.5];
    println!("turnover={:.12}", turnover(&positions));

    let is_slice = &sol_rets[200..500];
    let btc_slice: Vec<f64> = {
        let mut v = Vec::new();
        for ti in 201..501 {
            let c0 = panel.data[[ti - 1, btc_idx, close_idx]];
            let c1 = panel.data[[ti, btc_idx, close_idx]];
            v.push((c1 / c0).ln());
        }
        v
    };
    let obj = MultiTermObjective::default();
    let score = obj.score(is_slice, Some(&btc_slice), 3.0).unwrap();
    println!("multi_term_score={:.12}", score);
}
'''


def run_rust() -> Dict[str, List[float]]:
    """Write the Rust parity example at runtime (gitignored
    convention shared with parity_regime / parity_forex), build it,
    run it, parse key=value output."""
    src = REPO_RUST / "examples" / "_parity_panel.rs"
    src.write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release", "--features", "panel",
         "--example", "_parity_panel"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_panel"
    proc = subprocess.run(
        [str(bin_path), str(SRC_FIXTURES)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, List[float]] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, vals = line.split("=", 1)
        out[key] = [float(x) for x in vals.split(",")]
    return out


def run_python() -> Dict[str, List[float]]:
    """Drive the Python panel primitives in a subprocess and capture
    their outputs in the same key=value shape as the Rust binary."""
    driver = """
import sys
sys.path.insert(0, %r)
import numpy as np
from backtester.panel import (
    PanelData, load_panel,
    momentum_alpha, LongShortBasket,
)
from backtester.panel.sizing import erc_weights, equal_weights, _cov_from_returns
from backtester.panel.neutralize import (
    estimate_betas, estimate_vols,
    neutralize,
)
from backtester.panel.constraints import apply_constraints
from backtester.metrics import sortino, turnover
from backtester.objectives import MultiTermObjective

fixture_dir = %r
panel = load_panel({
    "SOL": f"{fixture_dir}/SOLUSDT_1h_30000_31000.csv",
    "BTC": f"{fixture_dir}/BTCUSDT_1h_jan_feb_2024.csv",
    "ETH": f"{fixture_dir}/ETHUSDT_1h_jan_feb_2024.csv",
})

def emit(k, vals):
    print(f"{k}=" + ",".join(f"{v:.12f}" for v in vals))

emit("eq_weights", equal_weights(len(panel.assets)).tolist())

# ERC at t=500 with 100-bar log-returns window.
close = panel.ds["close"].values
# Build the same 100-bar log-returns window the Rust side does:
# rows (t_end-100, t_end) of log(c_t / c_{t-1}).
T = 500
LB = 100
prev = close[T - LB - 1]
window = []
for ti in range(T - LB, T):
    row = np.log(close[ti] / prev)
    prev = close[ti]
    window.append(row)
window = np.array(window)
cov = _cov_from_returns(window)
erc = erc_weights(cov=cov)
emit("erc_weights", erc.tolist())

btc_idx = panel.assets.index("BTC")
betas = estimate_betas(window, market_idx=btc_idx)
vols = estimate_vols(window)
emit("betas", betas.tolist())
emit("vols", vols.tolist())

raw = np.array([1.0, -1.0, 1.0])
emit("neutralize_dollar", neutralize(raw, "dollar").tolist())
emit("neutralize_beta", neutralize(raw, "beta", betas=betas, market_idx=btc_idx).tolist())
emit("neutralize_sigma", neutralize(raw, "sigma", vols=vols).tolist())

# Basket: dollar-neutral momentum(20), n_long=1 n_short=1, at t=500.
basket = LongShortBasket(
    alpha_fn=momentum_alpha(20),
    neutralize_mode="dollar",
    n_long=1, n_short=1,
)
positions = basket.positions(panel, T)
emit("basket_dollar", [positions[a] for a in panel.assets])

# Constraints.
emit("constraints_cap_05_06_02_02",
     apply_constraints(np.array([0.6, 0.2, 0.2]), single_asset_max=0.5).tolist())
emit("constraints_cap_03_05_03_02",
     apply_constraints(np.array([0.5, 0.3, 0.2]), single_asset_max=0.3).tolist())
emit("constraints_gross_3",
     apply_constraints(np.array([2.0, -1.0, 1.5]), gross_lev_max=3.0).tolist())

# Sortino + turnover on SOL panel returns.
sol_idx = panel.assets.index("SOL")
sol_rets = np.diff(np.log(close[:, sol_idx]))
print(f"sortino_sol={sortino(sol_rets.tolist()):.12f}")
print(f"sortino_sol_ann252={sortino(sol_rets.tolist(), annualization=252):.12f}")
print(f"turnover={turnover(np.array([0.5, -0.5, 0.5, 0.0, 0.5])):.12f}")

# Multi-term score: SOL IS slice [200:500] vs BTC slice [201:501] log-rets
sol_slice = sol_rets[200:500].tolist()
btc_rets = np.diff(np.log(close[:, btc_idx]))
btc_slice = btc_rets[200:500].tolist()
obj = MultiTermObjective()
print(f"multi_term_score={obj(sol_slice, benchmark_rets=btc_slice, turnover=3.0):.12f}")
""" % (str(REPO_PY), str(SRC_FIXTURES))
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        env=env, cwd=REPO_PY, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Python run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, List[float]] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, vals = line.split("=", 1)
        out[key] = [float(x) for x in vals.split(",")]
    return out


# Per-key tolerance: closed-form math is tight; ERC is an iterative
# solver and Python uses scipy SLSQP while Rust uses Spinu fixed-point,
# so they only agree to ~1e-3 relative.
KEY_TOLS = {
    "eq_weights": 1e-12,
    "betas":      1e-9,
    "vols":       1e-9,
    "neutralize_dollar": 1e-12,
    "neutralize_beta":   1e-9,
    "neutralize_sigma":  1e-9,
    "basket_dollar":     1e-12,
    "constraints_cap_05_06_02_02": 1e-12,
    "constraints_cap_03_05_03_02": 1e-12,
    "constraints_gross_3":         1e-12,
    "sortino_sol":        1e-9,
    "sortino_sol_ann252": 1e-9,
    "turnover":           1e-12,
    "multi_term_score":   1e-9,
    "erc_weights":        1e-3,  # different solvers; loose tolerance
}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--strict", action="store_true",
                   help="Tighten ERC tolerance to 1e-6 (may fail on solver differences)")
    args = p.parse_args()

    print(f"Python repo: {REPO_PY}")
    print(f"Rust   repo: {REPO_RUST}")
    print(f"Fixtures   : {SRC_FIXTURES}")
    print()

    print("Running Python...")
    py = run_python()
    print(f"  emitted {len(py)} keys")
    print("Running Rust...")
    rs = run_rust()
    print(f"  emitted {len(rs)} keys")

    diffs = 0
    print(f"\n{'key':<35} {'tol':>8} {'max_rel':>10}  status")
    print("-" * 70)
    for key in sorted(set(py) | set(rs)):
        if key not in py:
            print(f"  {key:<33} ===  py-only missing")
            diffs += 1
            continue
        if key not in rs:
            print(f"  {key:<33} ===  rs-only missing")
            diffs += 1
            continue
        tol = KEY_TOLS.get(key, 1e-6)
        if args.strict and key == "erc_weights":
            tol = 1e-6
        a = py[key]
        b = rs[key]
        if len(a) != len(b):
            print(f"  {key:<33} ===  length mismatch py={len(a)} rs={len(b)}")
            diffs += 1
            continue
        max_rel = 0.0
        for xa, xb in zip(a, b):
            denom = max(abs(xa), abs(xb), 1e-12)
            rel = abs(xa - xb) / denom
            max_rel = max(max_rel, rel)
        ok = max_rel <= tol
        marker = "OK" if ok else "DIFF"
        print(f"  {key:<33} {tol:>8.0e} {max_rel:>10.2e}  [{marker}]")
        if not ok:
            diffs += 1

    print()
    if diffs == 0:
        print("PANEL CROSS-LANG PARITY OK")
        return 0
    print(f"PANEL CROSS-LANG PARITY FAIL: {diffs} keys mismatched")
    return 1


if __name__ == "__main__":
    sys.exit(main())
