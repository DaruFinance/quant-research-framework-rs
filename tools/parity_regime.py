#!/usr/bin/env python3
"""Focused regime-path parity check: USE_REGIME_SEG + USE_WFO at
otherwise-default settings. Isolates the regime+WFO code path from
forex/session interactions that ``parity_combo.py`` mixes in.

Usage
-----

    python tools/parity_regime.py
    python tools/parity_regime.py --tol 0.001    # strict 0.1%
    python tools/parity_regime.py --include costs sortino  # forward families

refactor: shares regex / subprocess / comparison logic via
``parity_common.py`` and ``parity_registry.json``. Behaviour unchanged
when invoked without ``--include``: the gate covers ``base`` + the
regime-extra WFO windows.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import parity_common as pc

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
    let cfg = Config::new();
    let regime_cfg = RegimeConfig::default();
    quant_research_framework_rs::run_with_regime_cfg(
        &bars, "Regime", ema_strategy, regime_cfg, cfg);
}
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path,
                   default=pc.REPO_RUST / "data" / "SOLUSDT_1h.csv")
    p.add_argument("--tol", type=float, default=0.001)
    p.add_argument("--include", nargs="*", default=[])
    args = p.parse_args()
    if not args.csv.exists():
        sys.stderr.write(f"need {args.csv}\n")
        return 2

    registry = pc.MetricRegistry.load()
    # parity_regime always includes 'regime' alongside 'base'; --include
    # adds optional forward-looking families on top.
    families = pc.resolve_families(registry, "base",
                                   ["regime"] + list(args.include))
    tags = registry.union_tags(families)
    fields = registry.union_fields(families)

    print(f"CSV         : {args.csv}")
    print(f"Families    : {families}")
    print(f"Flags       : USE_REGIME_SEG=True, USE_WFO=True, defaults otherwise")
    print(f"Tolerance   : {args.tol*100:.3f}%")

    print("\nRunning Python regime+WFO...")
    py = pc.parse_metrics(pc.run_python(
        args.csv,
        overrides={"USE_WFO": "True", "USE_REGIME_SEG": "True"},
    ))
    print(f"  parsed {len(py)} tagged lines")

    print("Running Rust   regime+WFO...")
    rs = pc.parse_metrics(pc.run_rust_example(
        "_parity_regime",
        pc.REPO_RUST / "examples" / "_parity_regime.rs",
        RUST_DRIVER,
        args.csv,
    ))
    print(f"  parsed {len(rs)} tagged lines")

    diffs = pc.compare(py, rs, tags, fields, args.tol)
    return 0 if diffs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
