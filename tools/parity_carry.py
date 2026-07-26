#!/usr/bin/env python3
"""Cross-language parity harness for the Phase 3 T6 carry pipeline.

Compares the Python `backtester.carry.{funding,basis,oi,onchain,
triggers,scheduler,models}` primitives against the Rust
`quant_research_framework_rs::carry` port on the four bundled DS-*
fixtures (funding 200evt / basis 24d / OI 168 1h / onchain 50d).

Cross-language tolerances: every primitive in T6 is closed-form
deterministic float math (point-in-time loaders, sign-tracking
triggers, scheduler integer arithmetic, persistent-sign / momentum /
oi-cointegration models), all should hit f64 numerical noise (<1e-12).
The script applies a 1e-9 closed-form tolerance internally.

Single-threaded by user request.

Usage:
    python tools/parity_carry.py
    python tools/parity_carry.py --tol 0.001

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
//! Cross-language parity harness binary (Phase 3 T6 carry).
//! Generated at runtime by tools/parity_carry.py.

#![cfg(feature = "carry")]

use quant_research_framework_rs::carry::{
    basis_at, load_basis, load_funding, load_oi, load_onchain,
    next_funding_time, oi_at, rate_at, value_at,
    BasisBlowoutTrigger, EventDrivenScheduler, FundingFlipTrigger,
    FundingMomentumModel, FundingOICointegrationModel,
    PersistentFundingSignModel, RebalanceKind,
    FUNDING_INTERVAL_S,
};

fn main() {
    let base = std::env::args().nth(1).expect("base dir arg");
    let funding = load_funding(format!("{}/funding.csv", base),
                                 "binance_perp", true).expect("load_funding");
    let basis = load_basis(format!("{}/basis.csv", base),
                             "btc_perp_spot", 0.01).expect("load_basis");
    let oi = load_oi(format!("{}/oi.csv", base), 3600, 60).expect("load_oi");
    let onchain = load_onchain(format!("{}/onchain.csv", base), "nvt")
        .expect("load_onchain");

    println!("funding_n={}", funding.events.len());
    println!("basis_n={}", basis.records.len());
    println!("oi_n={}", oi.records.len());
    println!("onchain_n={}", onchain.records.len());

    // ------------------------------------------------------------------
    // Point-in-time lookups at five timestamps inside each frame.
    // ------------------------------------------------------------------
    let funding_probes: Vec<i64> = (0..5)
        .map(|i| funding.events[20 + i * 30].time_s + 5)
        .collect();
    for (i, t) in funding_probes.iter().enumerate() {
        let r = rate_at(&funding, *t).unwrap_or(f64::NAN);
        println!("rate_at_{}_t={}", i, t);
        println!("rate_at_{}_v={:.12}", i, r);
    }

    let basis_probes: Vec<i64> = (0..5)
        .map(|i| basis.records[3 + i * 4].time_s)
        .collect();
    for (i, t) in basis_probes.iter().enumerate() {
        let r = basis_at(&basis, *t).unwrap();
        println!("basis_at_{}_t={}", i, t);
        println!("basis_at_{}_bp={:.12}", i, r.basis_bp);
    }

    let oi_probes: Vec<i64> = (0..5)
        .map(|i| oi.records[10 + i * 30].time_s + 1)
        .collect();
    for (i, t) in oi_probes.iter().enumerate() {
        let r = oi_at(&oi, *t).unwrap();
        println!("oi_at_{}_t={}", i, t);
        println!("oi_at_{}_v={:.12}", i, r.open_interest);
    }

    let onchain_probes: Vec<i64> = (0..5)
        .map(|i| onchain.records[5 + i * 8].time_s + 100)
        .collect();
    for (i, t) in onchain_probes.iter().enumerate() {
        let r = value_at(&onchain, *t).unwrap();
        println!("value_at_{}_t={}", i, t);
        println!("value_at_{}_v={:.12}", i, r.value);
    }

    // ------------------------------------------------------------------
    // Triggers, emit count + time of first 5 events.
    // ------------------------------------------------------------------
    let flip = FundingFlipTrigger::new(0.0).run(&funding);
    println!("funding_flip_n={}", flip.len());
    for (i, ev) in flip.iter().take(5).enumerate() {
        println!("funding_flip_{}_t={}", i, ev.time_s);
        println!("funding_flip_{}_dir={}", i, ev.direction);
        println!("funding_flip_{}_curr={:.12}", i, ev.curr);
    }

    let blow = BasisBlowoutTrigger::new(5, 1.5).unwrap().run(&basis);
    println!("basis_blowout_n={}", blow.len());
    for (i, ev) in blow.iter().take(5).enumerate() {
        println!("basis_blowout_{}_t={}", i, ev.time_s);
        println!("basis_blowout_{}_z={:.12}", i, ev.z.unwrap());
        println!("basis_blowout_{}_curr={:.12}", i, ev.curr);
    }

    // ------------------------------------------------------------------
    // Models: persistent_sign + momentum at five funding times.
    // ------------------------------------------------------------------
    let psm = PersistentFundingSignModel::new(3).unwrap();
    let mom = FundingMomentumModel::new(20, 1.5).unwrap();
    let coint = FundingOICointegrationModel::new(10, 1.0).unwrap();
    let model_probes: Vec<i64> = (0..5)
        .map(|i| funding.events[40 + i * 30].time_s)
        .collect();
    for (i, t) in model_probes.iter().enumerate() {
        let s = psm.signal_at(&funding, *t);
        println!("psm_{}_t={}", i, t);
        println!("psm_{}_dir={}", i, s.direction);
        println!("psm_{}_strength={:.12}", i, s.strength);
        let m = mom.signal_at(&funding, *t);
        println!("mom_{}_t={}", i, t);
        println!("mom_{}_dir={}", i, m.direction);
        println!("mom_{}_strength={:.12}", i, m.strength);
        let c = coint.signal_at(&funding, &oi, *t);
        println!("coint_{}_t={}", i, t);
        println!("coint_{}_dir={}", i, c.direction);
        println!("coint_{}_strength={:.12}", i, c.strength);
    }

    // ------------------------------------------------------------------
    // Scheduler: next_rebalance at five timestamps.
    // ------------------------------------------------------------------
    let sched = EventDrivenScheduler::new(
        Some(3600),
        Some(&funding),
        flip.clone(),
        funding.events[0].time_s,
        Some(funding.events[funding.events.len() - 1].time_s),
    );
    let sched_probes: Vec<i64> = (0..5)
        .map(|i| funding.events[10 + i * 25].time_s + 17)
        .collect();
    for (i, t) in sched_probes.iter().enumerate() {
        match sched.next_rebalance(*t) {
            Some(r) => {
                let kind = match r.kind {
                    RebalanceKind::Bar => "bar",
                    RebalanceKind::Funding => "funding",
                    RebalanceKind::Trigger => "trigger",
                };
                println!("sched_{}_t={}", i, t);
                println!("sched_{}_next_t={}", i, r.time_s);
                println!("sched_{}_kind={}", i, kind);
            }
            None => {
                println!("sched_{}_t={}", i, t);
                println!("sched_{}_next_t=-1", i);
                println!("sched_{}_kind=none", i);
            }
        }
    }

    // ------------------------------------------------------------------
    // Sundry: next_funding_time on a few inputs.
    // ------------------------------------------------------------------
    let nft_probes: Vec<i64> = vec![
        funding.events[0].time_s,
        funding.events[0].time_s + 1,
        funding.events[0].time_s + FUNDING_INTERVAL_S - 1,
        funding.events[0].time_s + FUNDING_INTERVAL_S,
    ];
    for (i, t) in nft_probes.iter().enumerate() {
        let n = next_funding_time(*t, FUNDING_INTERVAL_S);
        println!("nft_{}_t={}", i, t);
        println!("nft_{}_next={}", i, n);
    }
}
'''


def write_rust_fixtures(base_dir: Path) -> None:
    """Convert the four Python parquet/csv carry fixtures into CSVs
    the Rust loaders can read.  Schemas are kept identical (same
    column names) so the loaders can be source-of-truth-symmetric."""
    import pandas as pd
    fix = REPO_PY / "tests" / "fixtures"

    # Funding: time + rate (loader requires int + float).
    df = pd.read_parquet(fix / "funding_btcusdt_200evt.parquet")
    df = df[["time", "rate"]].copy()
    df["time"] = df["time"].astype("int64")
    df.to_csv(base_dir / "funding.csv", index=False)

    # Basis: time + close_spot + close_perp (+ basis_bp recomputed).
    df = pd.read_parquet(fix / "basis_btc_perp_spot_1d.parquet")
    cols = ["time", "close_spot", "close_perp"]
    if "basis_bp" in df.columns:
        cols.append("basis_bp")
    df = df[cols].copy()
    df["time"] = df["time"].astype("int64")
    df.to_csv(base_dir / "basis.csv", index=False)

    # OI: time + open_interest (+ open_interest_usd if present).
    df = pd.read_parquet(fix / "oi_btc_perp_1h_7d.parquet")
    cols = ["time", "open_interest"]
    if "open_interest_usd" in df.columns:
        cols.append("open_interest_usd")
    df = df[cols].copy()
    df["time"] = df["time"].astype("int64")
    df.to_csv(base_dir / "oi.csv", index=False)

    # On-chain: already CSV, with column "nvt": copy verbatim.
    src = fix / "onchain_nvt_50d.csv"
    (base_dir / "onchain.csv").write_bytes(src.read_bytes())


def run_rust(base_dir: Path) -> Dict[str, str]:
    src = REPO_RUST / "examples" / "_parity_carry.rs"
    src.write_text(RUST_DRIVER)
    build = subprocess.run(
        ["cargo", "build", "--jobs", "1", "--release",
         "--features", "carry", "--example", "_parity_carry"],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / "_parity_carry"
    proc = subprocess.run(
        [str(bin_path), str(base_dir)],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key] = val
    return out


def run_python(base_dir: Path) -> Dict[str, str]:
    """Mirror the Rust binary's emissions key-for-key."""
    driver = f'''
import sys
sys.path.insert(0, {str(REPO_PY)!r})
from backtester.carry.funding import (
    FUNDING_INTERVAL_S, load_funding, next_funding_time, rate_at,
)
from backtester.carry.basis import basis_at, load_basis
from backtester.carry.oi import load_oi, oi_at
from backtester.carry.onchain import load_onchain, value_at
from backtester.carry.triggers import (
    BasisBlowoutTrigger, FundingFlipTrigger,
)
from backtester.carry.scheduler import EventDrivenScheduler
from backtester.carry.models import (
    FundingMomentumModel, FundingOICointegrationModel,
    PersistentFundingSignModel,
)

base = {str(base_dir)!r}
funding = load_funding(f"{{base}}/funding.csv", venue="binance_perp",
                        strict_boundary=True)
basis = load_basis(f"{{base}}/basis.csv", instrument_pair="btc_perp_spot",
                     recompute_basis_tol_bp=0.01)
oi = load_oi(f"{{base}}/oi.csv", expected_cadence_s=3600, cadence_tol_s=60)
onchain = load_onchain(f"{{base}}/onchain.csv", metric="nvt")

print(f"funding_n={{len(funding)}}")
print(f"basis_n={{len(basis)}}")
print(f"oi_n={{len(oi)}}")
print(f"onchain_n={{len(onchain)}}")

# ----- point-in-time lookups -----
funding_probes = [int(funding["time"].iloc[20 + i * 30]) + 5 for i in range(5)]
for i, t in enumerate(funding_probes):
    r = rate_at(funding, t)
    print(f"rate_at_{{i}}_t={{t}}")
    print(f"rate_at_{{i}}_v={{r:.12f}}")

basis_probes = [int(basis["time"].iloc[3 + i * 4]) for i in range(5)]
for i, t in enumerate(basis_probes):
    r = basis_at(basis, t)
    print(f"basis_at_{{i}}_t={{t}}")
    print(f"basis_at_{{i}}_bp={{r.basis_bp:.12f}}")

oi_probes = [int(oi["time"].iloc[10 + i * 30]) + 1 for i in range(5)]
for i, t in enumerate(oi_probes):
    r = oi_at(oi, t)
    print(f"oi_at_{{i}}_t={{t}}")
    print(f"oi_at_{{i}}_v={{r.open_interest:.12f}}")

onchain_probes = [int(onchain["time"].iloc[5 + i * 8]) + 100 for i in range(5)]
for i, t in enumerate(onchain_probes):
    r = value_at(onchain, t)
    print(f"value_at_{{i}}_t={{t}}")
    print(f"value_at_{{i}}_v={{r.value:.12f}}")

# ----- triggers -----
flip = FundingFlipTrigger(min_magnitude=0.0).run(funding)
print(f"funding_flip_n={{len(flip)}}")
for i, ev in enumerate(flip[:5]):
    print(f"funding_flip_{{i}}_t={{ev.time_s}}")
    print(f"funding_flip_{{i}}_dir={{ev.direction}}")
    print(f"funding_flip_{{i}}_curr={{ev.curr:.12f}}")

blow = BasisBlowoutTrigger(window=5, z_thresh=1.5).run(basis)
print(f"basis_blowout_n={{len(blow)}}")
for i, ev in enumerate(blow[:5]):
    print(f"basis_blowout_{{i}}_t={{ev.time_s}}")
    print(f"basis_blowout_{{i}}_z={{ev.extras['z']:.12f}}")
    print(f"basis_blowout_{{i}}_curr={{ev.curr:.12f}}")

# ----- models -----
psm = PersistentFundingSignModel(min_streak=3)
mom = FundingMomentumModel(window=20, z_thresh=1.5)
coint = FundingOICointegrationModel(window=10, scale=1.0)
model_probes = [int(funding["time"].iloc[40 + i * 30]) for i in range(5)]
for i, t in enumerate(model_probes):
    s = psm.signal_at(funding, t)
    print(f"psm_{{i}}_t={{t}}")
    print(f"psm_{{i}}_dir={{s.direction}}")
    print(f"psm_{{i}}_strength={{s.strength:.12f}}")
    m = mom.signal_at(funding, t)
    print(f"mom_{{i}}_t={{t}}")
    print(f"mom_{{i}}_dir={{m.direction}}")
    print(f"mom_{{i}}_strength={{m.strength:.12f}}")
    c = coint.signal_at(funding, oi, t)
    print(f"coint_{{i}}_t={{t}}")
    print(f"coint_{{i}}_dir={{c.direction}}")
    print(f"coint_{{i}}_strength={{c.strength:.12f}}")

# ----- scheduler -----
sched = EventDrivenScheduler(
    bar_cadence_s=3600,
    funding_df=funding,
    triggers=flip,
    t_start_s=int(funding["time"].iloc[0]),
    t_end_s=int(funding["time"].iloc[-1]),
)
sched_probes = [int(funding["time"].iloc[10 + i * 25]) + 17 for i in range(5)]
for i, t in enumerate(sched_probes):
    nxt = sched.next_rebalance(t)
    if nxt is None:
        print(f"sched_{{i}}_t={{t}}")
        print(f"sched_{{i}}_next_t=-1")
        print(f"sched_{{i}}_kind=none")
    else:
        print(f"sched_{{i}}_t={{t}}")
        print(f"sched_{{i}}_next_t={{nxt.time_s}}")
        print(f"sched_{{i}}_kind={{nxt.kind}}")

# ----- next_funding_time -----
t0 = int(funding["time"].iloc[0])
nft_probes = [t0, t0 + 1, t0 + FUNDING_INTERVAL_S - 1, t0 + FUNDING_INTERVAL_S]
for i, t in enumerate(nft_probes):
    print(f"nft_{{i}}_t={{t}}")
    print(f"nft_{{i}}_next={{next_funding_time(t)}}")
'''
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Python run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    out: Dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key] = val
    return out


def cmp_value(key: str, py: str, rs: str, tol: float) -> tuple[bool, str]:
    """Compare values.  Try float first; fall back to string equality."""
    try:
        pv = float(py)
        rv = float(rs)
    except ValueError:
        return (py == rs, f"py={py}  rs={rs}")
    if pv == rv:
        return (True, f"py={pv:.12f}  rs={rv:.12f}  EXACT")
    denom = max(abs(pv), abs(rv), 1e-15)
    rel = abs(pv - rv) / denom
    return (rel <= tol, f"py={pv:.12f}  rs={rv:.12f}  rel={rel:.2e}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tol", type=float, default=1e-9,
                          help="closed-form tolerance (default 1e-9)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="qrf_parity_carry_") as tmp:
        base_dir = Path(tmp)
        write_rust_fixtures(base_dir)
        rs = run_rust(base_dir)
        py = run_python(base_dir)

    keys = sorted(set(rs.keys()) | set(py.keys()))
    failures: List[str] = []
    print(f"Tolerance: {args.tol:.0e}")
    for k in keys:
        if k not in rs or k not in py:
            print(f"  {k}: MISSING (py={k in py}, rs={k in rs})")
            failures.append(k)
            continue
        ok, msg = cmp_value(k, py[k], rs[k], args.tol)
        marker = "[OK]" if ok else "[FAIL]"
        print(f"  {k}: {msg}  {marker}")
        if not ok:
            failures.append(k)

    if failures:
        print(f"\nCARRY PARITY FAILED: {len(failures)} mismatch(es)")
        return 1
    print("\nCARRY PARITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
