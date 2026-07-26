#!/usr/bin/env python3
"""Cross-language parity harness for PBO / CSCV (part D).

Compares Python ``backtester.pbo`` against the Rust ``pbo`` module
(feature ``overfit``) on a battery of fixed ``(T, N)`` equity matrices.
PBO is fully deterministic given the matrix (rank/logit arithmetic, no
RNG), so this is a clean closed-form parity in the ``parity_dsr.py``
pattern: a ``%.17g`` fixture is the single source of truth; the Rust
example is generated at runtime.

Cases include a NON-power-of-two S (S=14, T not divisible by S) so the
fold-edge logic is actually exercised, without it the harness only ever
hits the clean S in {8,16} path and a fold-edge bug ships latent
(Lens C D6 / Lens B D1).

Gate defaults to 1e-9; pass ``--tol 1e-3`` for the paper gate.
Single-threaded by user request.

Usage:
    python tools/parity_pbo.py
    python tools/parity_pbo.py --tol 0.001
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


def build_cases() -> List[Tuple[str, int, np.ndarray]]:
    rng = np.random.default_rng(20260611)
    cases: List[Tuple[str, int, np.ndarray]] = []

    # 1. Clean N=8 panel, T=200, S=8.
    drift = rng.normal(0.0005, 0.01, size=(200, 8)).cumsum(axis=0) + 1.0
    cases.append(("clean_n8_s8", 8, drift))

    # 2. Same panel at S=16 (the paper value).
    cases.append(("clean_n8_s16", 16, drift.copy()))

    # 3. N=2 panel, S=8 (degenerate-rank coverage).
    t = 120
    a = np.linspace(1.0, 2.0, t)
    b = np.linspace(1.0, 2.0, t)[::-1] + 1.0
    m2 = np.column_stack([a, b])
    cases.append(("n2_panel", 8, m2))

    # 4. Ties in OOS ranks (stable argsort tiebreak).
    base = rng.normal(0.0003, 0.008, size=(160, 3)).cumsum(axis=0) + 1.0
    tied = np.column_stack([base, base[:, 0], base[:, 1]])
    cases.append(("ties_n5", 8, tied))

    # 5. NON-power-of-two S=14, T=187 (T % S != 0) -> exercises fold edges.
    nonpow = rng.normal(0.0004, 0.011, size=(187, 6)).cumsum(axis=0) + 1.0
    cases.append(("nonpow2_s14", 14, nonpow))

    # 6. Larger N=12 panel, S=8.
    big = rng.normal(0.0004, 0.012, size=(300, 12)).cumsum(axis=0) + 1.0
    cases.append(("n12_s8", 8, big))

    return cases


def fmt_row(xs: np.ndarray) -> str:
    return ",".join("%.17g" % float(x) for x in xs)


def write_fixture(path: Path, cases) -> None:
    lines = ["# name|S|T|N|<row0 csv>;<row1 csv>;...  (%.17g; parity_pbo.py)"]
    for name, S, M in cases:
        T, N = M.shape
        rows = ";".join(fmt_row(M[t]) for t in range(T))
        lines.append(f"{name}|{S}|{T}|{N}|{rows}")
    path.write_text("\n".join(lines) + "\n")


def parse_matrix(T: int, N: int, body: str) -> np.ndarray:
    rows = body.split(";")
    M = np.empty((T, N), dtype=float)
    for t, row in enumerate(rows):
        M[t] = [float(tok) for tok in row.split(",")]
    return M


def run_python(fixture: Path) -> Dict[str, float]:
    sys.path.insert(0, str(REPO_PY))
    from backtester.pbo import cscv  # noqa: E402

    out: Dict[str, float] = {}
    for line in fixture.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, S_s, T_s, N_s, body = line.split("|")
        S, T, N = int(S_s), int(T_s), int(N_s)
        M = parse_matrix(T, N, body)
        res = cscv(M, S=S)
        out[f"{name}_pbo"] = float(res["pbo"])
        out[f"{name}_nsplits"] = float(res["n_splits"])
    return out


RUST_DRIVER = r'''//! Cross-language parity harness binary (item #3, PBO/CSCV).
//! Generated at runtime by tools/parity_pbo.py.
#![cfg(feature = "overfit")]

use quant_research_framework_rs::pbo::cscv;

fn parse_matrix(t: usize, n: usize, body: &str) -> Vec<Vec<f64>> {
    let mut m = vec![vec![0.0f64; n]; t];
    for (ti, row) in body.split(';').enumerate() {
        for (ni, tok) in row.split(',').enumerate() {
            m[ti][ni] = tok.trim().parse::<f64>().expect("parse f64");
        }
    }
    m
}

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture");
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.splitn(5, '|').collect();
        assert!(parts.len() == 5, "bad fixture line: {line}");
        let name = parts[0].trim();
        let s: usize = parts[1].trim().parse().expect("S");
        let t: usize = parts[2].trim().parse().expect("T");
        let n: usize = parts[3].trim().parse().expect("N");
        let m = parse_matrix(t, n, parts[4]);
        let res = cscv(&m, s);
        println!("{name}_pbo={:.12}", res.pbo);
        println!("{name}_nsplits={:.12}", res.n_splits as f64);
    }
}
'''


def run_rust(fixture: Path) -> Dict[str, float]:
    (REPO_RUST / "examples" / "_parity_pbo.rs").write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release",
         "--features", "overfit", "--example", "_parity_pbo"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-3000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_pbo"
    proc = subprocess.run(
        [str(bin_path), str(fixture)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-3000:]}\n")
        sys.exit(2)
    out: Dict[str, float] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key] = float(val)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-9,
                    help="relative tolerance (default 1e-9; paper gate 1e-3)")
    ap.add_argument("--atol", type=float, default=1e-12,
                    help="absolute tolerance floor for near-zero values")
    args = ap.parse_args()

    cases = build_cases()
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "pbo_fixtures.txt"
        write_fixture(fixture, cases)
        py = run_python(fixture)
        rs = run_rust(fixture)

    keys = sorted(set(py) | set(rs))
    n_ok = n_bad = 0
    for k in keys:
        if k not in py or k not in rs:
            print(f"  MISSING {k}: py={k in py} rs={k in rs}")
            n_bad += 1
            continue
        a, b = py[k], rs[k]
        if math.isnan(a) or math.isnan(b):
            if math.isnan(a) and math.isnan(b):
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
                  f"rel={rel:.3e} abs={abs_diff:.3e} > tol={args.tol:.0e}")
            n_bad += 1

    status = "OK" if n_bad == 0 else "FAIL"
    print(f"parity_pbo: {n_ok}/{len(keys)} metric points within tol={args.tol:.0e} "
          f"({len(cases)} cases) -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
