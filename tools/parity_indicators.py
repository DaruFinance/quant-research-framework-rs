#!/usr/bin/env python3
"""Cross-language parity harness for the shared TradingView/pandas indicators
(roadmap item 5, indicator parity).

Locks EVERY shared indicator Python-vs-Rust on a real-shaped OHLC fixture so
the framework's "match on everything" brand claim is ENFORCED, not merely
claimed. Indicators covered (Python `backtester/indicators.py`  vs  Rust
`quant_research_framework_rs::indicators`):

    compute_sma, compute_ema, compute_macd (macd + signal),
    compute_rsi, compute_atr, compute_stoch
    + indicator-of-indicator:  ema(ATR), sma(ATR)   (locks the NaN-aware ema)

Each indicator is compared over its FULL output vector (not just the
steady-state tail) at several lengths, so warmup-region defects (e.g. an
RSI that emits values where pandas emits NaN, or an ATR with the wrong
first-valid index) cannot hide. NaN matches NaN; a finite-vs-NaN mismatch
is a failure.

The fixture is deliberately engineered to FORCE each edge branch (so a
regression actually has a disagreeing point to trip on, rather than the
random walk merely happening to agree):
  * pure-flat FROM BAR 0          -> RSI avg_loss==0 -> pandas NaN (not 100)
  * 28-bar embedded flat block    -> Stochastic 0/0 -> NaN at L=14 AND L=21
  * one malformed hi==lo,close!=lo -> Stochastic +/-inf (both sides)
  * one NaN-injected high          -> Stochastic NaN-window propagation
  * ema(ATR), sma(ATR)             -> indicator-of-indicator NaN-aware path

Like `parity_dsr.py`, these indicators are pure functions on already-known
bars (no future data, no engine stdout), so this uses the scalar-emit
style: each (indicator, length, bar_index) point is one `key=value` line.

The fixture file (written with %.17g per float) is the single source of
truth: Python reads it back and the Rust example reads it, so both sides
receive bit-identical inputs. Agreement is expected at f64 noise (<1e-12);
the gate defaults to 1e-9 and accepts --tol (paper gate 1e-3) with an
absolute floor for the near-zero region (MACD/RSI crossing through ~0).

Single-threaded by user request (cargo --jobs 1).

Usage:
    python tools/parity_indicators.py
    python tools/parity_indicators.py --tol 0.001

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
from typing import Dict, List

import numpy as np
import pandas as pd

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(
    os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework")
)

# Indicator lengths to exercise. Mix of short (warmup-heavy) and standard.
SMA_LENGTHS = [3, 14]
EMA_LENGTHS = [14, 50]
RSI_LENGTHS = [14, 7]
ATR_LENGTHS = [14, 20]
STOCH_LENGTHS = [14, 21]
# MACD param triples (fast, slow, signal): the library default and a short one.
MACD_PARAMS = [(12, 26, 9), (5, 13, 4)]
# Indicator-of-indicator: ema/sma OF an ATR (which has a leading-NaN warmup).
IOI_ATR_LENGTH = 14
IOI_EMA_SPAN = 50
IOI_SMA_LENGTH = 3

# Fixture layout indices (kept in sync with build_fixture()).
FLAT0_LEN = 30          # pure-flat from bar 0 -> forces RSI avg_loss==0 -> NaN
FLAT_BLOCK_LEN = 28     # embedded flat block -> Stochastic 0/0 at L=14 and 21


def build_fixture() -> pd.DataFrame:
    """A real-shaped OHLC fixture engineered to force every edge branch.

    Layout (~310 bars):
      [0:30)      pure-flat from bar 0 (close==open==high==low) -> RSI NaN
      [30:180)    seeded random walk with gaps -> true-range != range
      [180:208)   28-bar flat block (hi==lo==close) -> Stochastic 0/0 -> NaN
      [208:]      more walk
    Plus two engineered single-bar defects in the walk region:
      * one malformed bar (hi==lo but close>lo) -> Stochastic +/-inf
      * one NaN-injected high                   -> Stochastic NaN-window
    """
    rng = np.random.default_rng(20260611)

    def walk(n: int, start: float) -> np.ndarray:
        steps = rng.normal(0.0, 1.0, n) * (start * 0.01)
        return start + np.cumsum(steps)

    flat0 = np.full(FLAT0_LEN, 100.0)               # pure-flat from bar 0
    close_a = walk(150, 100.0)
    flat = np.full(FLAT_BLOCK_LEN, float(close_a[-1]))
    close_b = walk(100, float(flat[-1]))
    close = np.concatenate([flat0, close_a, flat, close_b])
    n = len(close)

    # Build OHLC with gaps (so true-range != range and the ATR fix matters).
    rng2 = np.random.default_rng(20260612)
    rang = np.abs(rng2.normal(0.0, 1.0, n)) * (close * 0.004) + 1e-6
    gap = rng2.normal(0.0, 1.0, n) * (close * 0.002)
    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1] + gap[1:]
    high = np.maximum(open_, close) + rang
    low = np.minimum(open_, close) - rang

    # Pure-flat-from-0 block: hi==lo==close==open so deltas are 0 from bar 0
    # -> avg_gain==avg_loss==0 exactly -> pandas RSI = NaN (not 100). This is
    # the branch the old geometric-decay-only flat block never reached.
    high[:FLAT0_LEN] = close[:FLAT0_LEN]
    low[:FLAT0_LEN] = close[:FLAT0_LEN]
    open_[:FLAT0_LEN] = close[:FLAT0_LEN]

    # Embedded flat block (28 bars >= max(RSI=14, STOCH=21) + margin): a truly
    # flat window forces Stochastic numerator and denominator both 0 -> 0/0 ->
    # NaN, at BOTH L=14 and L=21 (a 20-bar block never formed a 21-bar window).
    fb_lo = FLAT0_LEN + 150
    fb_hi = fb_lo + FLAT_BLOCK_LEN
    high[fb_lo:fb_hi] = close[fb_lo:fb_hi]
    low[fb_lo:fb_hi] = close[fb_lo:fb_hi]
    open_[fb_lo:fb_hi] = close[fb_lo:fb_hi]

    # Malformed bar in the walk region: hi==lo but close strictly above ->
    # Stochastic denominator 0, numerator != 0 -> +/-inf on both sides.
    mal = fb_hi + 10
    high[mal] = low[mal] = close[mal] - 1.0   # window can see hi==lo, close>lo

    # NaN-injected high a few bars later: pandas rolling max -> NaN -> K NaN;
    # Rust must skip the window (not swallow the NaN via f64::max).
    nanbar = mal + 8
    high[nanbar] = np.nan

    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def fmt_csv(xs) -> str:
    out = []
    for x in xs:
        x = float(x)
        if math.isnan(x):
            out.append("nan")
        elif x == math.inf:
            out.append("inf")
        elif x == -math.inf:
            out.append("-inf")
        else:
            out.append("%.17g" % x)
    return ",".join(out)


def write_fixture(path: Path, df: pd.DataFrame) -> None:
    lines = ["# open|high|low|close  (%.17g; written by parity_indicators.py)"]
    lines.append(
        "|".join(
            fmt_csv(df[c].to_numpy()) for c in ("open", "high", "low", "close")
        )
    )
    path.write_text("\n".join(lines) + "\n")


def parse_csv(field: str) -> List[float]:
    field = field.strip()
    if not field:
        return []
    return [float(tok) for tok in field.split(",")]


def read_fixture(path: Path) -> pd.DataFrame:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        o, h, l, c = line.split("|")
        return pd.DataFrame(
            {
                "open": parse_csv(o),
                "high": parse_csv(h),
                "low": parse_csv(l),
                "close": parse_csv(c),
            }
        )
    raise RuntimeError("no data line in fixture")


def emit_key(out: Dict[str, float], prefix: str, vec) -> None:
    """Store every element of a vector under prefix#<index> so the full
    output (including the warmup NaN region) is compared, not just the tail."""
    for i, v in enumerate(np.asarray(vec, dtype=float)):
        out[f"{prefix}#{i}"] = float(v)


def run_python(fixture: Path) -> Dict[str, float]:
    sys.path.insert(0, str(REPO_PY))
    from backtester.indicators import (  # noqa: E402
        compute_sma,
        compute_ema,
        compute_macd,
        compute_rsi,
        compute_atr,
        compute_stoch,
    )

    df = read_fixture(fixture)
    out: Dict[str, float] = {}
    for L in SMA_LENGTHS:
        emit_key(out, f"sma_{L}", compute_sma(df, L))
    for L in EMA_LENGTHS:
        emit_key(out, f"ema_{L}", compute_ema(df, L))
    for f, s, sig in MACD_PARAMS:
        macd, signal = compute_macd(df, f, s, sig)
        emit_key(out, f"macd_{f}_{s}_{sig}", macd)
        emit_key(out, f"macdsig_{f}_{s}_{sig}", signal)
    for L in RSI_LENGTHS:
        emit_key(out, f"rsi_{L}", compute_rsi(df, L))
    for L in ATR_LENGTHS:
        emit_key(out, f"atr_{L}", compute_atr(df, L))
    for L in STOCH_LENGTHS:
        emit_key(out, f"stoch_{L}", compute_stoch(df, L))

    # Indicator-of-indicator: ema/sma OF an ATR (leading-NaN warmup input).
    # This is the path the NaN-aware `ema()` exists for; pandas computes it as
    # the span-EMA / rolling-mean of the ATR series directly.
    atr = compute_atr(df, IOI_ATR_LENGTH)            # pandas Series w/ NaN head
    ioi_ema = atr.ewm(span=IOI_EMA_SPAN, adjust=False).mean()
    ioi_sma = atr.rolling(IOI_SMA_LENGTH).mean()
    emit_key(out, f"ioi_ema_atr{IOI_ATR_LENGTH}_{IOI_EMA_SPAN}", ioi_ema)
    emit_key(out, f"ioi_sma_atr{IOI_ATR_LENGTH}_{IOI_SMA_LENGTH}", ioi_sma)
    return out


# The Rust parity driver. examples/_parity_*.rs is gitignored (auto-generated
# scaffolding), so we write it here before building: matching parity_dsr.py.
RUST_DRIVER = r'''//! Cross-language parity harness binary (roadmap item 5, indicators).
//! Generated at runtime by tools/parity_indicators.py.
//!
//! Reads a fixture file (path = argv[1]): a single data line
//! `<open csv>|<high csv>|<low csv>|<close csv>`, numbers written by the
//! Python harness with %.17g so parsing back to f64 is bit-identical. Emits
//! one `key=value` line per (indicator, length, bar_index) point, mirroring
//! the Python side. NaN is emitted as the literal `nan` and +/-inf as
//! `inf`/`-inf` on both sides.

#![cfg(feature = "indicators")]

use quant_research_framework_rs::indicators::{
    compute_atr, compute_ema, compute_macd, compute_rsi, compute_sma, compute_stoch, ema,
};

fn fmt(v: f64) -> String {
    if v.is_nan() {
        "nan".to_string()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".to_string() } else { "-inf".to_string() }
    } else {
        format!("{:.12}", v)
    }
}

fn parse_csv(field: &str) -> Vec<f64> {
    let field = field.trim();
    if field.is_empty() {
        return Vec::new();
    }
    field
        .split(',')
        .map(|s| {
            let s = s.trim();
            match s {
                "nan" => f64::NAN,
                "inf" => f64::INFINITY,
                "-inf" => f64::NEG_INFINITY,
                _ => s.parse::<f64>().expect("parse f64"),
            }
        })
        .collect()
}

fn emit(prefix: &str, vec: &[f64]) {
    for (i, v) in vec.iter().enumerate() {
        println!("{prefix}#{i}={}", fmt(*v));
    }
}

const SMA_LENGTHS: &[usize] = &[3, 14];
const EMA_LENGTHS: &[usize] = &[14, 50];
const RSI_LENGTHS: &[usize] = &[14, 7];
const ATR_LENGTHS: &[usize] = &[14, 20];
const STOCH_LENGTHS: &[usize] = &[14, 21];
const MACD_PARAMS: &[(usize, usize, usize)] = &[(12, 26, 9), (5, 13, 4)];
const IOI_ATR_LENGTH: usize = 14;
const IOI_EMA_SPAN: usize = 50;
const IOI_SMA_LENGTH: usize = 3;

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture file");
    let mut data_line = "";
    for line in contents.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        data_line = t;
        break;
    }
    let parts: Vec<&str> = data_line.split('|').collect();
    assert!(parts.len() == 4, "bad fixture line");
    let _open = parse_csv(parts[0]);
    let high = parse_csv(parts[1]);
    let low = parse_csv(parts[2]);
    let close = parse_csv(parts[3]);

    for &l in SMA_LENGTHS {
        emit(&format!("sma_{l}"), &compute_sma(&close, l));
    }
    for &l in EMA_LENGTHS {
        emit(&format!("ema_{l}"), &compute_ema(&close, l));
    }
    for &(f, s, sig) in MACD_PARAMS {
        let (macd, signal) = compute_macd(&close, f, s, sig);
        emit(&format!("macd_{f}_{s}_{sig}"), &macd);
        emit(&format!("macdsig_{f}_{s}_{sig}"), &signal);
    }
    for &l in RSI_LENGTHS {
        emit(&format!("rsi_{l}"), &compute_rsi(&close, l));
    }
    for &l in ATR_LENGTHS {
        emit(&format!("atr_{l}"), &compute_atr(&high, &low, &close, l));
    }
    for &l in STOCH_LENGTHS {
        emit(&format!("stoch_{l}"), &compute_stoch(&high, &low, &close, l));
    }

    // Indicator-of-indicator: ema/sma OF an ATR (leading-NaN warmup input).
    // Uses the NaN-aware `ema` (NOT compute_ema) so the leading-NaN ATR head
    // is skipped, matching pandas `atr.ewm(span,adjust=False).mean()`.
    let atr = compute_atr(&high, &low, &close, IOI_ATR_LENGTH);
    emit(
        &format!("ioi_ema_atr{IOI_ATR_LENGTH}_{IOI_EMA_SPAN}"),
        &ema(&atr, IOI_EMA_SPAN),
    );
    emit(
        &format!("ioi_sma_atr{IOI_ATR_LENGTH}_{IOI_SMA_LENGTH}"),
        &compute_sma(&atr, IOI_SMA_LENGTH),
    );
}
'''


def run_rust(fixture: Path) -> Dict[str, float]:
    (REPO_RUST / "examples" / "_parity_indicators.rs").write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release",
         "--features", "indicators", "--example", "_parity_indicators"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-3000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_indicators"
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
        out[key] = float(val)  # float("nan"/"inf"/"-inf") all parse
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-9,
                    help="relative tolerance (default 1e-9; paper gate 1e-3)")
    ap.add_argument("--atol", type=float, default=1e-9,
                    help="absolute tolerance floor for near-zero values "
                         "(MACD/RSI can cross through ~0 where the relative "
                         "metric explodes; the absolute gap is f64 noise)")
    args = ap.parse_args()

    df = build_fixture()
    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "indicator_fixtures.txt"
        write_fixture(fixture, df)
        py = run_python(fixture)
        rs = run_rust(fixture)

    keys = sorted(set(py) | set(rs))
    n_ok = 0
    n_bad = 0
    fam_bad: Dict[str, int] = {}

    def fam(k: str) -> str:
        return k.split("#", 1)[0]

    def both_inf(a: float, b: float) -> bool:
        return math.isinf(a) and math.isinf(b) and (a > 0) == (b > 0)

    for k in keys:
        if k not in py or k not in rs:
            print(f"  MISSING {k}: py={k in py} rs={k in rs}")
            n_bad += 1
            fam_bad[fam(k)] = fam_bad.get(fam(k), 0) + 1
            continue
        a, b = py[k], rs[k]
        a_nan, b_nan = math.isnan(a), math.isnan(b)
        if a_nan or b_nan:
            if a_nan and b_nan:
                n_ok += 1
            else:
                print(f"  DIFF {k}: py={a!r} rs={b!r} (NaN mismatch, warmup/guard)")
                n_bad += 1
                fam_bad[fam(k)] = fam_bad.get(fam(k), 0) + 1
            continue
        if math.isinf(a) or math.isinf(b):
            if both_inf(a, b):
                n_ok += 1
            else:
                print(f"  DIFF {k}: py={a!r} rs={b!r} (inf mismatch)")
                n_bad += 1
                fam_bad[fam(k)] = fam_bad.get(fam(k), 0) + 1
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
            fam_bad[fam(k)] = fam_bad.get(fam(k), 0) + 1

    status = "OK" if n_bad == 0 else "FAIL"
    if fam_bad:
        print("  failing families: " + ", ".join(
            f"{f}={c}" for f, c in sorted(fam_bad.items())))
    print(f"parity_indicators: {n_ok}/{len(keys)} indicator points within "
          f"tol={args.tol:.0e} ({len(df)} bars) -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
