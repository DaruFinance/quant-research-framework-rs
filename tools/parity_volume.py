#!/usr/bin/env python3
"""Cross-language parity harness for the volume indicators + one volume
strategy (v0.6.0). Mirrors tools/parity_dsr.py.

Compares Python `backtester.volume_indicators` against the Rust
`quant_research_framework_rs::volume` mirror, one full volume strategy
(vol-confirmed EMA cross), AND the session-reset producer (ny_session_resets),
on data/volume_fixture.csv.

Two session-reset checks:
  1. CONSUMER: Python resolves reset flags and injects them into the Rust
     driver so vwap_session is tested on identical flags.
  2. PRODUCER: the Rust driver ALSO emits its own ny_session_resets result,
     which we diff against Python's flags element-wise (this is the path the
     real vwap_mean_reversion example uses standalone in each engine).

Emit format mirrors parity_dsr but the Rust driver formats every float as
{:.17e} (17 sig figs, magnitude-independent) so small relvol/z-score values
round-trip at the 1e-9 default gate.

Single-threaded (cargo --jobs 1) by user request.

    python tools/parity_volume.py
    python tools/parity_volume.py --tol 0.001   # paper gate

Exit 0 = parity OK, 1 = mismatch, 2 = build/run error.
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))
FIXTURE = REPO_RUST / "data" / "volume_fixture.csv"

ANCHOR_HOUR = 0
VOL_LEN = 20
MFI_LEN = 14
VWAP_ROLL = 20
ZS_LEN = 20
FAST, SLOW, K = 12, 26, 1.5
STRIDE = 53


def _load_df() -> pd.DataFrame:
    sys.path.insert(0, str(REPO_PY))
    from backtester import load_ohlc  # noqa: E402
    df = load_ohlc(str(FIXTURE))
    # Reset flags are computed from the sorted Python df and fed positionally
    # to the Rust driver (file order). They align ONLY if timestamps are
    # strictly increasing (sort is a no-op). Assert it loudly.
    assert df['time'].is_monotonic_increasing, \
        "fixture timestamps must be strictly increasing for reset-flag alignment"
    return df


def run_python(df: pd.DataFrame, reset: np.ndarray) -> Dict[str, float]:
    from backtester import volume_indicators as vi  # noqa: E402

    series = {
        "obv": vi.obv(df),
        "ad": vi.ad_line(df),
        "volsma": vi.volume_sma(df, VOL_LEN),
        "volema": vi.volume_ema(df, VOL_LEN),
        "relvol": vi.relative_volume(df, VOL_LEN),
        "volz": vi.volume_zscore(df, ZS_LEN),
        "mfi": vi.mfi(df, MFI_LEN),
        "vwapr": vi.vwap_rolling(df, VWAP_ROLL),
        "vwaps": vi.vwap_session(df, reset),
    }
    out: Dict[str, float] = {}
    n = len(df)
    for name, arr in series.items():
        for i in range(0, n, STRIDE):
            out[f"{name}_{i}"] = float(arr[i])
    # session-reset PRODUCER cross-check (full vector, not strided)
    for i in range(n):
        out[f"reset_{i}"] = 1.0 if bool(reset[i]) else 0.0
    # one full strategy
    sig = _py_vol_ema_cross(df)
    out["strat_sum"] = float(int(sig.astype(np.int64).sum()))
    out["strat_nz"] = float(int((sig != 0).sum()))
    for i in range(0, n, STRIDE):
        out[f"strat_{i}"] = float(sig[i])
    return out


def _py_vol_ema_cross(df: pd.DataFrame) -> np.ndarray:
    from backtester import volume_indicators as vi
    n = len(df)
    raw = np.zeros(n, dtype=np.int8)
    if n < 3:
        return raw
    close = df['close']
    fast = close.ewm(span=FAST, adjust=False).mean().to_numpy()
    slow = close.ewm(span=SLOW, adjust=False).mean().to_numpy()
    vol = df['volume'].to_numpy()
    vsma = vi.volume_sma(df, VOL_LEN)
    for i in range(2, n):
        if np.isnan(vsma[i - 1]) or not (vol[i - 1] > K * vsma[i - 1]):
            continue
        cu = fast[i - 1] > slow[i - 1] and fast[i - 2] <= slow[i - 2]
        cd = fast[i - 1] < slow[i - 1] and fast[i - 2] >= slow[i - 2]
        if cu:
            raw[i] = 1
        elif cd:
            raw[i] = -1
    return raw


# Rust parity driver. examples/_parity_volume.rs is gitignored (auto-generated).
RUST_DRIVER = r'''//! Volume parity driver. Generated at runtime by
//! tools/parity_volume.py. argv[1] = OHLCV csv, argv[2] = injected reset-flags
//! file (one 0/1 per bar). Emits `key=value` lines, floats as {:.17e}.

use quant_research_framework_rs::volume::{
    ad_line, mfi, ny_session_resets, obv, relative_volume, volume_ema,
    volume_sma, volume_zscore, vwap_rolling, vwap_session,
};
use quant_research_framework_rs::{compute_ema, load_ohlc, Bar};

const ANCHOR_HOUR: u32 = 0;
const VOL_LEN: usize = 20;
const MFI_LEN: usize = 14;
const VWAP_ROLL: usize = 20;
const ZS_LEN: usize = 20;
const STRIDE: usize = 53;
const FAST: usize = 12;
const SLOW: usize = 26;
const K: f64 = 1.5;

fn fmt(v: f64) -> String {
    if v.is_nan() { "nan".to_string() } else { format!("{:.17e}", v) }
}

fn vol_ema_cross(bars: &[Bar], vol: &[f64]) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < 3 { return raw; }
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = compute_ema(&close, FAST);
    let slow = compute_ema(&close, SLOW);
    let vsma = volume_sma(vol, VOL_LEN);
    for i in 2..n {
        if vsma[i - 1].is_nan() || !(vol[i - 1] > K * vsma[i - 1]) { continue; }
        let cu = fast[i - 1] > slow[i - 1] && fast[i - 2] <= slow[i - 2];
        let cd = fast[i - 1] < slow[i - 1] && fast[i - 2] >= slow[i - 2];
        if cu { raw[i] = 1; } else if cd { raw[i] = -1; }
    }
    raw
}

fn main() {
    let csv = std::env::args().nth(1).expect("csv path");
    let reset_path = std::env::args().nth(2).expect("reset flags path");
    let bars = load_ohlc(&csv);
    let vol: Vec<f64> = bars.iter().map(|b| b.volume).collect();
    let times: Vec<i64> = bars.iter().map(|b| b.time_unix).collect();
    let injected: Vec<bool> = std::fs::read_to_string(&reset_path)
        .expect("read reset flags")
        .split_whitespace()
        .map(|s| s.trim() == "1")
        .collect();
    assert_eq!(injected.len(), bars.len(), "reset flag count mismatch");

    // PRODUCER path: Rust's own ny_session_resets (what the example uses).
    let own_reset = ny_session_resets(&times, ANCHOR_HOUR);

    let series: Vec<(&str, Vec<f64>)> = vec![
        ("obv", obv(&bars, &vol)),
        ("ad", ad_line(&bars, &vol)),
        ("volsma", volume_sma(&vol, VOL_LEN)),
        ("volema", volume_ema(&vol, VOL_LEN)),
        ("relvol", relative_volume(&vol, VOL_LEN)),
        ("volz", volume_zscore(&vol, ZS_LEN)),
        ("mfi", mfi(&bars, &vol, MFI_LEN)),
        ("vwapr", vwap_rolling(&bars, &vol, VWAP_ROLL)),
        ("vwaps", vwap_session(&bars, &vol, &injected)),
    ];
    let n = bars.len();
    for (name, arr) in &series {
        let mut i = 0;
        while i < n {
            println!("{name}_{i}={}", fmt(arr[i]));
            i += STRIDE;
        }
    }
    for i in 0..n {
        println!("reset_{i}={}", if own_reset[i] { "1.00000000000000000e0" } else { "0.00000000000000000e0" });
    }
    let sig = vol_ema_cross(&bars, &vol);
    let s: i64 = sig.iter().map(|&x| x as i64).sum();
    let nz = sig.iter().filter(|&&x| x != 0).count();
    println!("strat_sum={}", fmt(s as f64));
    println!("strat_nz={}", fmt(nz as f64));
    let mut i = 0;
    while i < n {
        println!("strat_{i}={}", fmt(sig[i] as f64));
        i += STRIDE;
    }
}
'''


def run_rust(reset: np.ndarray) -> Dict[str, float]:
    (REPO_RUST / "examples" / "_parity_volume.rs").write_text(RUST_DRIVER)
    reset_file = REPO_RUST / "data" / "_volume_reset_flags.txt"
    reset_file.write_text(" ".join("1" if r else "0" for r in reset.tolist()))
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release", "--example", "_parity_volume"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-3000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_volume"
    proc = subprocess.run(
        [str(bin_path), str(FIXTURE), str(reset_file)],
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
        out[key] = float(val)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=1e-9,
                    help="relative tolerance (default 1e-9; paper gate 1e-3)")
    ap.add_argument("--atol", type=float, default=1e-9,
                    help="absolute floor for near-zero values")
    args = ap.parse_args()

    if not FIXTURE.exists():
        # The fixture is deterministic and untracked; auto-generate it so this
        # harness is self-contained on a fresh checkout / in CI / under make repro.
        sys.stderr.write(f"fixture missing: {FIXTURE}, generating via "
                         f"tools/make_volume_fixture.py ...\n")
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import make_volume_fixture
        make_volume_fixture.main()
    if not FIXTURE.exists():
        sys.stderr.write(f"fixture generation failed: {FIXTURE}\n")
        return 2

    df = _load_df()
    from backtester import volume_indicators as vi
    reset = vi.ny_session_resets(df, ANCHOR_HOUR)

    py = run_python(df, reset)
    rs = run_rust(reset)

    keys = sorted(set(py) | set(rs))
    n_ok = n_bad = 0
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
    print(f"parity_volume: {n_ok}/{len(keys)} points within tol={args.tol:.0e} -> {status}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
