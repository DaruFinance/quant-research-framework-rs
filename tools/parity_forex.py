#!/usr/bin/env python3
"""Forex-mode parity check — runs both engines with FOREX_MODE=True on
the bundled EURUSD 1h dataset. Validates pip-aware sizing, funding-skip
semantics, and R-unit PnL math under the standard tag set.

Usage
-----

    python tools/parity_forex.py                       # data/EURUSD_1h.csv
    python tools/parity_forex.py --csv path.csv
    python tools/parity_forex.py --tol 0.001
    python tools/parity_forex.py --include costs sortino

Item #15 refactor: shares regex / subprocess / comparison logic via
``parity_common.py`` and ``parity_registry.json``. Behaviour unchanged
when invoked without ``--include``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import parity_common as pc

RUST_DRIVER = """
use quant_research_framework_rs::{Bar, Config, compute_ema, load_ohlc, run_cfg};

fn ema_strategy(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = compute_ema(&close, 20);
    let slow = compute_ema(&close, lb);
    let n = bars.len();
    let mut raw = vec![0i8; n];
    for i in 1..n {
        if fast[i - 1].is_nan() || slow[i - 1].is_nan() { continue; }
        raw[i] = if fast[i - 1] > slow[i - 1] { 1 }
                 else if fast[i - 1] < slow[i - 1] { -1 } else { 0 };
    }
    raw
}

fn main() {
    let csv = std::env::args().nth(1).unwrap_or_else(|| "data/EURUSD_1h.csv".into());
    let bars = load_ohlc(&csv);
    println!("Loaded {} bars from {}", bars.len(), csv);
    let mut cfg = Config::new().with_forex_defaults();
    // JPY pairs use pip_size=0.01; everything else stays at the 0.0001 default.
    // Mirrors `bt.PIP_SIZE = 0.01 if "JPY" in bt.CSV_FILE else 0.0001` on the
    // Python side. Without this, JPY datasets parity-fail by ~50% on roi/sharpe
    // because the Rust side runs with EUR-scale stops on a JPY-scale series.
    if csv.to_uppercase().contains("JPY") { cfg.pip_size = 0.01; }
    run_cfg(&bars, "EMA-crossover", ema_strategy, cfg);
}
"""

PY_FOREX_SETUP = """
bt.FOREX_MODE = True
bt.PIP_SIZE = 0.01 if "JPY" in bt.CSV_FILE else 0.0001
bt.SL_PERCENTAGE *= bt.PIP_SIZE
bt.TP_PERCENTAGE *= bt.PIP_SIZE
bt.RISK_AMOUNT = 1.0
bt.ACCOUNT_SIZE = 1.0
bt.POSITION_SIZE = 1.0
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path,
                   default=pc.REPO_RUST / "data" / "EURUSD_1h.csv")
    p.add_argument("--tol", type=float, default=0.001)
    p.add_argument("--include", nargs="*", default=[])
    args = p.parse_args()
    if not args.csv.exists():
        sys.stderr.write(f"need {args.csv}\n")
        return 2

    registry = pc.MetricRegistry.load()
    families = pc.resolve_families(registry, "base", args.include)
    tags = registry.union_tags(families)
    fields = registry.union_fields(families)

    print(f"CSV         : {args.csv}")
    print(f"Families    : {families}")
    print(f"Flags       : FOREX_MODE=True + pip-aware SL/TP")
    print(f"Tolerance   : {args.tol*100:.3f}%")

    print("\nRunning Python forex...")
    py = pc.parse_metrics(pc.run_python(
        args.csv,
        extra_setup=PY_FOREX_SETUP,
    ))
    print(f"  parsed {len(py)} tagged lines")

    print("Running Rust   forex...")
    rs = pc.parse_metrics(pc.run_rust_example(
        "_parity_forex",
        pc.REPO_RUST / "examples" / "_parity_forex.rs",
        RUST_DRIVER,
        args.csv,
    ))
    print(f"  parsed {len(rs)} tagged lines")

    diffs = pc.compare(py, rs, tags, fields, args.tol)
    return 0 if diffs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
