#!/usr/bin/env python3
"""Forex-mode parity check — extends `parity_check.py` to FOREX_MODE=True
on both engines (no sessions, no regime). Validates pip-aware sizing and
funding-skip semantics under the standard tag set.

Usage:
    python tools/parity_forex.py                     # uses data/EURUSD_1h.csv
    python tools/parity_forex.py --csv path.csv      # custom dataset
    python tools/parity_forex.py --tol 0.001         # 0.1% rel tolerance
"""
from __future__ import annotations
import argparse, os, re, subprocess, sys
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
    r"MaxDD:\s*\$?(?P<dd>\-?[\d,]+\.\d+)R?")

def parse(s):
    out = {}
    for ln in s.splitlines():
        m = LINE_RE.match(ln)
        if not m: continue
        out[m.group("tag").strip()] = {
            "trades": int(m.group("trades")),
            "roi":    float(m.group("roi").replace(",","")),
            "pf":     float("inf") if m.group("pf")=="inf" else float(m.group("pf")),
            "sharpe": float(m.group("shp")),
            "win":    float(m.group("win"))/100,
            "exp":    float(m.group("exp").replace(",","")),
            "max_dd": float(m.group("dd").replace(",","")),
        }
    return out

def run_py(csv):
    env = os.environ.copy(); env["BT_CSV"] = str(Path(csv).resolve()); env["MPLBACKEND"] = "Agg"
    drv = """
import sys; sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO = False
bt.FOREX_MODE = True
bt.PIP_SIZE = 0.01 if "JPY" in bt.CSV_FILE else 0.0001
bt.SL_PERCENTAGE *= bt.PIP_SIZE
bt.TP_PERCENTAGE *= bt.PIP_SIZE
bt.RISK_AMOUNT = 1.0; bt.ACCOUNT_SIZE = 1.0; bt.POSITION_SIZE = 1.0
bt.main()
""" % str(REPO_PY)
    p = subprocess.run([sys.executable,"-c",drv], env=env, cwd=REPO_PY,
                       capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        sys.stderr.write(f"PY FAIL:\n{p.stderr[-2000:]}\n"); sys.exit(2)
    return p.stdout

def run_rs(csv):
    # Write a one-shot example that flips FOREX_MODE on via run_cfg, build it,
    # run it. Matches the parity_combo.py pattern for throwaway harness bins.
    src = REPO_RUST / "examples" / "_parity_forex.rs"
    src.write_text("""
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
    let cfg = Config::new().with_forex_defaults();
    run_cfg(&bars, "EMA-crossover", ema_strategy, cfg);
}
""")
    b = subprocess.run(["cargo","build","--release","--example","_parity_forex"],
                       cwd=REPO_RUST, capture_output=True, text=True, timeout=600)
    if b.returncode != 0:
        sys.stderr.write(f"BUILD FAIL:\n{b.stderr[-2000:]}\n"); sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_forex"
    p = subprocess.run([str(bin_path), str(csv)], cwd=REPO_RUST,
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        sys.stderr.write(f"RS FAIL:\n{p.stderr[-2000:]}\n"); sys.exit(2)
    return p.stdout

def cmp(py, rs, tol):
    tags = ["IS-raw","OOS-raw","IS-opt","OOS-opt","Baseline IS","Baseline OOS",
            "W01 IS","W01 OOS"]
    fails = 0
    for tag in tags:
        if tag not in py or tag not in rs:
            print(f"  [{tag}] missing"); fails += 1; continue
        print(f"  [{tag}]")
        for k in ("trades","roi","pf","sharpe","win","exp","max_dd"):
            a, b = py[tag][k], rs[tag][k]
            if k == "trades":
                ok = a == b; tag_ = "OK" if ok else "MISMATCH"
                print(f"    {k:>8}: py={a}  rs={b}  [{tag_}]")
                if not ok: fails += 1
                continue
            denom = max(abs(a),abs(b),1e-9); rel = abs(a-b)/denom
            ok = rel <= tol or (abs(a)<1e-6 and abs(b)<1e-6)
            tag_ = "OK" if ok else "MISMATCH"
            print(f"    {k:>8}: py={a:>12.4f}  rs={b:>12.4f}  rel={rel:6.2%}  [{tag_}]")
            if not ok: fails += 1
    return fails

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path,
                   default=REPO_RUST / "data" / "EURUSD_1h.csv")
    p.add_argument("--tol", type=float, default=0.001)
    a = p.parse_args()
    print(f"Python repo: {REPO_PY}")
    print(f"Rust   repo: {REPO_RUST}")
    print(f"CSV        : {a.csv}")
    print(f"Tolerance  : {a.tol*100:.3f}%\n")
    print("Running Python...")
    py = parse(run_py(a.csv))
    print(f"  parsed {len(py)} tags")
    print("Running Rust...")
    rs = parse(run_rs(a.csv))
    print(f"  parsed {len(rs)} tags\n")
    fails = cmp(py, rs, a.tol)
    if fails:
        print(f"\n  PARITY FAIL: {fails} mismatches outside ±{a.tol*100:.1f}%")
        return 1
    print("\n  PARITY OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
