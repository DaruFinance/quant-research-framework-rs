#!/usr/bin/env python3
"""Four-way parity GATE: regime + WFO + forex + session, both engines.

This script layers all four features at once (USE_REGIME_SEG + USE_WFO +
FOREX_MODE + TRADE_SESSIONS) and asserts the two engines agree within 1e-3
relative tolerance (trade counts exactly), exiting non-zero on any
mismatch. It is the fourth parity surface, complementing:
  * tools/parity_check.py   — default config (56/56 metric points)
  * tools/parity_regime.py  — USE_REGIME_SEG + USE_WFO (98/98 points)
  * tools/parity_forex.py   — FOREX_MODE on EURUSD 1h (56/56 points)

Both engines are driven with the SAME selection knobs (MIN_TRADES and
OPTIMIZE_RRR are compile-time constants on the Rust side, so the Python
driver keeps the shared defaults rather than overriding them — an earlier
override was the dominant cause of the combo divergence).

History (roadmap item 08): the combo was a known-failing diagnostic. Two
causes were found and fixed — (1) the MIN_TRADES/OPTIMIZE_RRR driver
mismatch above, and (2) three session-end-bar handling divergences in the
Rust backtest core (it let opposite-flip entries through, never blocked a
new entry when flat, and ran the SL/TP check on the closing bar, whereas
Python force-closes unconditionally and skips SL/TP). With both fixed the
combo is byte-exact on EURUSD 1h across all 14 stages x 7 fields. See
docs/verification/combo_fourway_item08.md.

Usage:
    python tools/parity_combo.py                       # EURUSD 1h, tol 1e-3
    python tools/parity_combo.py --csv data/SOLUSDT_1h.csv
Exit 0 = parity OK, 1 = mismatch, 2 = setup failure.
"""
from __future__ import annotations

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
    env["BT_CSV"] = str(csv); env["MPLBACKEND"] = "Agg"
    driver = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO   = False
bt.USE_WFO           = True
bt.USE_REGIME_SEG    = True
bt.FOREX_MODE        = True
bt.TRADE_SESSIONS    = True
bt.SESSION_START     = "8:00"
bt.SESSION_END       = "16:50"
# MIN_TRADES and OPTIMIZE_RRR are compile-time constants on the Rust side
# (MIN_TRADES=10, OPTIMIZE_RRR=true); the parity comparison must use the
# SAME values on both engines, so we keep the shared defaults here rather
# than overriding them. The earlier 1 / False overrides were the sole cause
# of the combo divergence: they changed which LB the IS phase selected.
bt.MIN_TRADES        = 10
bt.OPTIMIZE_RRR      = True
# Pip-mode side-effects that backtester.py applies at import time when
# FOREX_MODE was already True. We re-apply them here because we flipped
# the flag at runtime instead.
import os
bt.PIP_SIZE = 0.01 if "JPY" in bt.CSV_FILE else 0.0001
bt.SL_PERCENTAGE *= bt.PIP_SIZE
bt.TP_PERCENTAGE *= bt.PIP_SIZE
bt.RISK_AMOUNT  = 1.0
bt.ACCOUNT_SIZE = 1.0
bt.POSITION_SIZE = 1.0
bt.main()
""" % str(REPO_PY)
    proc = subprocess.run([sys.executable, "-c", driver], env=env,
                          cwd=REPO_PY, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        sys.stderr.write(f"Python failed:\n{proc.stderr[-2000:]}\n"); sys.exit(2)
    return proc.stdout


def run_rust_combo(csv: Path) -> str:
    """Build a one-shot example binary that exercises run_with_regime
    with use_forex + use_sessions both on, then run it."""
    src = REPO_RUST / "examples" / "_parity_combo.rs"
    src.write_text("""
use quant_research_framework_rs::{
    Bar, Config, RegimeConfig, default_regime_detector, compute_ema, load_ohlc,
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

    // Combo: regime + WFO + forex + session. WFO is implicit in run_with_regime.
    let mut cfg = Config::new();
    cfg = cfg.with_forex(true).with_sessions(true, 8, 17);
    let regime_cfg = RegimeConfig::default();   // 3-regime EMA-200 / 8-bar

    // Inline run_with_regime so we can pass our pre-built cfg through.
    quant_research_framework_rs::run_with_regime_cfg(&bars, "Combo", ema_strategy, regime_cfg, cfg);
}
""")
    # We need a `run_with_regime_cfg` entry that accepts a pre-built Config.
    # Add a thin wrapper if it isn't present (idempotent).
    lib = REPO_RUST / "src" / "lib.rs"
    text = lib.read_text()
    if "pub fn run_with_regime_cfg" not in text:
        wrapper = '''

/// Like `run_with_regime` but takes a pre-built `Config` so callers can
/// flip use_forex / use_sessions / use_oos2 / etc. before the engine runs.
pub fn run_with_regime_cfg(
    bars: &[Bar], strategy: &str, sig_fn: RawSignalsFn,
    regime_cfg: RegimeConfig, mut cfg: Config,
) {
    let total_start = std::time::Instant::now();
    let bars = age_dataset(bars.to_vec(), AGE_DATASET);
    let base = classic_single_run(&bars, &mut cfg, strategy, sig_fn);
    println!(" Baseline Optimized Metrics ");
    if let Some(ref met) = base.met_is_opt { prettyprint("Baseline IS", met, base.best_lb); }
    if let Some(ref met) = base.met_oos_opt { prettyprint("Baseline OOS", met, base.best_lb); }
    run_robustness_tests(&bars, base.best_lb, base.best_rrr, &cfg, sig_fn);
    println!("\\n Running Walk-Forward Windows (regime-rotated LB) ");
    walk_forward_regime(&bars, &mut cfg, &regime_cfg, &base.eq_is_raw);
    println!("\\nTotal runtime: {:.2}s", total_start.elapsed().as_secs_f64());
}
'''
        lib.write_text(text + wrapper)

    # Build & run the one-shot example.
    build = subprocess.run(
        ["cargo", "build", "--release", "--example", "_parity_combo"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n"); sys.exit(2)
    proc = subprocess.run(
        ["cargo", "run", "--release", "--example", "_parity_combo", "--", str(csv)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        sys.stderr.write(f"Rust failed:\n{proc.stderr[-2000:]}\n"); sys.exit(2)
    return proc.stdout


def report(py: dict, rs: dict, tags: list[str], tol: float) -> int:
    """Compare and return the number of failing (tag, field) points.
    Trade counts must match exactly; floats within `tol` relative."""
    print(f"\nMetric comparison ({len(tags)} tags, tol={tol:.0e}):\n")
    failures = 0
    for tag in tags:
        if tag not in py or tag not in rs:
            print(f"  [{tag}]  MISSING (py={tag in py} rs={tag in rs})")
            failures += 1
            continue
        print(f"  [{tag}]")
        for k in ("trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"):
            p = py[tag][k]; r = rs[tag][k]
            if k == "trades":
                ok = p == r
                print(f"    {k:>8}: py={p}  rs={r}  [{'OK' if ok else 'DIFF'}]")
                if not ok: failures += 1
                continue
            denom = max(abs(p), abs(r), 1e-9)
            rel = abs(p - r) / denom
            ok = rel <= tol or (abs(p) < 1e-6 and abs(r) < 1e-6)
            print(f"    {k:>8}: py={p:>14.4f}  rs={r:>14.4f}  rel={rel:6.2%}  [{'OK' if ok else 'DIFF'}]")
            if not ok: failures += 1
    return failures


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, default=REPO_RUST / "data" / "EURUSD_1h.csv",
                    help="OHLC CSV (default EURUSD_1h, where forex pip sizing is "
                         "economically meaningful)")
    ap.add_argument("--tol", type=float, default=1e-3,
                    help="relative tolerance for float metrics (default 1e-3, "
                         "matching the published parity surfaces)")
    args = ap.parse_args()
    csv = args.csv
    if not csv.exists():
        print(f"need {csv}"); return 2
    print(f"CSV: {csv}\nFlags: regime + WFO + forex + session (all ON)\n")

    print("Running Python combo...")
    py = parse_metrics(run_python(csv))
    print(f"  parsed {len(py)} tagged lines")
    print("Running Rust   combo...")
    rs = parse_metrics(run_rust_combo(csv))
    print(f"  parsed {len(rs)} tagged lines")

    # Tags that should be present in both engines for this combo.
    tags = ["IS-raw", "OOS-raw", "IS-opt", "OOS-opt",
            "Baseline IS", "Baseline OOS",
            "W01 IS", "W01 OOS", "W02 IS", "W02 OOS"]
    failures = report(py, rs, tags, args.tol)
    if failures == 0:
        print(f"\n  COMBO PARITY OK ({len(tags)} stages x 7 fields within tol)")
        return 0
    print(f"\n  COMBO PARITY FAIL: {failures} points outside tol")
    return 1


if __name__ == "__main__":
    sys.exit(main())
