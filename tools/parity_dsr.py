#!/usr/bin/env python3
"""Cross-language parity harness for the Deflated Sharpe Ratio (item 09).

Compares the Python ``backtester.dsr`` post-processing utility against the
Rust ``quant_research_framework_rs::dsr`` mirror on a battery of fixture
cases that exercise the clean path, skewed / fat-tailed returns, several
trial counts (both ``Phi^{-1}`` terms), and every NaN / 0.0 guard branch.

DSR is a closed-form post-hoc statistic on already-realised returns: it
consumes no future bars and runs outside the engine, so there is no
look-ahead surface and no stdout-metric line. This script therefore uses
the scalar key=value emit style (like ``parity_carry.py``), NOT the
``LINE_RE`` stdout-metric registry.

The fixture file written here (``%.17g`` per float) is the single source of
truth: it is read back and fed to Python, and read by the Rust example, so
both sides receive bit-identical inputs. Agreement is expected at f64 noise
(<1e-12); the gate defaults to 1e-9 and accepts ``--tol``. NaN matches NaN.

Single-threaded by user request.

Usage:
    python tools/parity_dsr.py
    python tools/parity_dsr.py --tol 0.001

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
from typing import Dict, List, Tuple

import numpy as np

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(
    os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework")
)


def build_cases() -> List[Tuple[str, float, List[float], List[float]]]:
    """Return (name, sharpe_chosen, trial_sharpes, returns) tuples.

    Returns arrays are drawn from seeded distributions so the file is
    deterministic across runs; the file itself is the source of truth, so
    the exact draws need only be reproducible, not analytically special.
    """
    rng = np.random.default_rng(20260531)
    cases: List[Tuple[str, float, List[float], List[float]]] = []

    # 1. Clean / Gaussian-ish returns, moderate trial grid.
    cases.append((
        "clean",
        0.9,
        [0.1, 0.4, 0.9, 0.6, 0.3, 0.8],
        list(rng.normal(0.001, 0.01, 250)),
    ))
    # 2. Left-skewed, fat-tailed returns (exercise g_3, g_4).
    fat = rng.standard_t(df=4, size=400) * 0.01 - 0.02 * rng.random(400)
    cases.append(("fat_tailed", 1.4, list(rng.normal(0.5, 0.3, 30)), list(fat)))
    # 3. Large trial grid -> Phi^{-1}(1 - 1/(N e)) deep in the tail.
    cases.append((
        "many_trials",
        1.1,
        list(rng.normal(0.2, 0.25, 500)),
        list(rng.normal(0.0008, 0.012, 180)),
    ))
    # 4. Minimal trial grid (N = 2) -> both PPF terms near the boundary.
    cases.append((
        "two_trials",
        0.7,
        [0.3, 0.95],
        list(rng.normal(0.0005, 0.009, 120)),
    ))
    # 5. Negative chosen Sharpe.
    cases.append((
        "neg_sharpe",
        -0.6,
        [0.1, -0.2, 0.4, 0.05],
        list(rng.normal(-0.0004, 0.011, 90)),
    ))

    # --- guard branches ---------------------------------------------------
    good_rets = list(rng.normal(0.0, 0.01, 50))
    # 6. N < 2 finite trials -> SR_0 == 0.0 (and DSR uses SR_0 = 0).
    cases.append(("guard_one_trial", 0.8, [0.5], good_rets))
    # 7. Zero trial variance -> SR_0 == 0.0.
    cases.append(("guard_zerovar_trials", 0.8, [0.5, 0.5, 0.5, 0.5], good_rets))
    # 8. t < 3 returns -> DSR NaN.
    cases.append(("guard_few_returns", 1.0, [0.1, 0.2, 0.3], [0.01, 0.02]))
    # 9. Non-finite chosen Sharpe -> DSR NaN.
    cases.append(("guard_nan_sharpe", float("nan"), [0.1, 0.2, 0.3], good_rets))
    # 10. Zero-dispersion returns -> sd == 0 -> DSR NaN.
    cases.append(("guard_const_returns", 1.0, [0.1, 0.2, 0.3], [0.5] * 12))
    # 11. Non-finite returns dropped (NaN/inf interleaved) -> matches clean
    #     subset on both sides.
    interleaved = [0.01, float("nan"), -0.02, float("inf"), 0.03, 0.0, 0.015, 0.004]
    cases.append(("guard_nonfinite_returns", 0.9, [0.2, 0.6, 0.4], interleaved))

    return cases


def fmt_csv(xs: List[float]) -> str:
    out = []
    for x in xs:
        if isinstance(x, float) and math.isnan(x):
            out.append("nan")
        elif x == math.inf:
            out.append("inf")
        elif x == -math.inf:
            out.append("-inf")
        else:
            out.append("%.17g" % x)
    return ",".join(out)


def write_fixture(path: Path, cases) -> None:
    lines = ["# name|sharpe|trials|returns  (%.17g; written by parity_dsr.py)"]
    for name, sharpe, trials, returns in cases:
        s = "nan" if math.isnan(sharpe) else "%.17g" % sharpe
        lines.append(f"{name}|{s}|{fmt_csv(trials)}|{fmt_csv(returns)}")
    path.write_text("\n".join(lines) + "\n")


def parse_csv(field: str) -> List[float]:
    field = field.strip()
    if not field:
        return []
    return [float(tok) for tok in field.split(",")]


def run_python(fixture: Path) -> Dict[str, float]:
    sys.path.insert(0, str(REPO_PY))
    from backtester.dsr import (  # noqa: E402
        deflated_sharpe_ratio,
        expected_max_sharpe_under_null,
    )

    out: Dict[str, float] = {}
    for line in fixture.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, s, trials_s, returns_s = line.split("|")
        sharpe = float(s)
        trials = parse_csv(trials_s)
        returns = parse_csv(returns_s)
        out[f"{name}_sr0"] = expected_max_sharpe_under_null(trials)
        out[f"{name}_dsr"] = deflated_sharpe_ratio(sharpe, trials, returns)
    return out


# The Rust parity driver. examples/_parity_*.rs is gitignored (auto-generated
# scaffolding), so we write it here before building: matching parity_carry.py.
RUST_DRIVER = r'''//! Cross-language parity harness binary (roadmap item 09, DSR).
//! Generated at runtime by tools/parity_dsr.py.
//!
//! Reads a fixture file (path = argv[1]) of pipe-delimited cases and emits
//! one `key=value` line per scalar, mirroring the Python side. Each line is
//! `<name>|<sharpe>|<trial_sharpes csv>|<returns csv>`; numbers are written
//! by the Python harness with %.17g so parsing back to f64 is bit-identical.
//! NaN is emitted as the literal `nan` on both sides.

#![cfg(feature = "dsr")]

use quant_research_framework_rs::dsr::{deflated_sharpe_ratio, expected_max_sharpe_under_null};

fn fmt(v: f64) -> String {
    if v.is_nan() { "nan".to_string() } else { format!("{:.12}", v) }
}

fn parse_csv(field: &str) -> Vec<f64> {
    let field = field.trim();
    if field.is_empty() {
        return Vec::new();
    }
    field.split(',').map(|s| s.trim().parse::<f64>().expect("parse f64")).collect()
}

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture file");
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.split('|').collect();
        assert!(parts.len() == 4, "bad fixture line: {line}");
        let name = parts[0].trim();
        let sharpe: f64 = parts[1].trim().parse().expect("parse sharpe");
        let trials = parse_csv(parts[2]);
        let returns = parse_csv(parts[3]);
        let sr0 = expected_max_sharpe_under_null(&trials);
        let dsr = deflated_sharpe_ratio(sharpe, &trials, &returns);
        println!("{name}_sr0={}", fmt(sr0));
        println!("{name}_dsr={}", fmt(dsr));
    }
}
'''


def run_rust(fixture: Path) -> Dict[str, float]:
    (REPO_RUST / "examples" / "_parity_dsr.rs").write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release",
         "--features", "dsr", "--example", "_parity_dsr"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-3000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_dsr"
    proc = subprocess.run(
        [str(bin_path), str(fixture)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-3000:]}\n")
        sys.exit(2)
    out: Dict[str, float] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key] = float(val)  # float("nan") parses "nan"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-9,
                    help="relative tolerance (default 1e-9; paper gate 1e-3)")
    ap.add_argument("--atol", type=float, default=1e-12,
                    help="absolute tolerance floor for near-zero values "
                         "(deep-tail Phi: scipy yields a ~1e-15 denormal where "
                         "statrs flushes to 0.0; the absolute gap is f64 noise)")
    args = ap.parse_args()

    cases = build_cases()
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "dsr_fixtures.txt"
        write_fixture(fixture, cases)
        py = run_python(fixture)
        rs = run_rust(fixture)

    keys = sorted(set(py) | set(rs))
    n_ok = 0
    n_bad = 0
    for k in keys:
        if k not in py or k not in rs:
            print(f"  MISSING {k}: py={k in py} rs={k in rs}")
            n_bad += 1
            continue
        a, b = py[k], rs[k]
        a_nan, b_nan = math.isnan(a), math.isnan(b)
        if a_nan or b_nan:
            if a_nan and b_nan:
                n_ok += 1
            else:
                print(f"  DIFF {k}: py={a!r} rs={b!r} (NaN mismatch)")
                n_bad += 1
            continue
        abs_diff = abs(a - b)
        denom = max(abs(a), abs(b), 1e-12)
        rel = abs_diff / denom
        if rel <= args.tol or abs_diff <= args.atol:
            n_ok += 1
        else:
            print(f"  DIFF {k}: py={a:.15g} rs={b:.15g} "
                  f"rel={rel:.3e} abs={abs_diff:.3e} > tol={args.tol:.0e}/atol={args.atol:.0e}")
            n_bad += 1

    status = "OK" if n_bad == 0 else "FAIL"
    print(f"parity_dsr: {n_ok}/{len(keys)} metric points within tol={args.tol:.0e} "
          f"({len(cases)} cases) -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
