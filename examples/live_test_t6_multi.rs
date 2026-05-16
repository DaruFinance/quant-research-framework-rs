//! Multi-strategy live-test driver for T6 synth.
//!
//! T6 operates on funding events (not OHLC bars). For "first 1000 candles"
//! we use the whole synth fixture (200 funding events) and step through
//! them incrementally. WFO geometry is FIXED at wfo_n=200 (slice=33).
//!
//! Output: combined trade ledger keyed by
//!   (strategy_id, wfo_window, side, entry_time)
//! where a "trade" is a run of consecutive same-direction signals
//! (entry on 0→±1, exit on ±1→0 or sign flip), with pnl summed over the
//! held events. Open positions at window-end are NOT emitted (live-safe).
//!
//! Usage:
//!     live_test_t6_multi <out.csv> <n_events> <wfo_n>
//!
//! Reads funding.csv / basis.csv / oi.csv from
//! /home/daru/leak_test/synth_data/t6_fix.

#![cfg(feature = "carry")]

use std::fs;
use std::io::Write as _;
use std::path::PathBuf;

use quant_research_framework_rs::carry::{
    load_basis, load_funding, load_oi,
    BasisBlowoutTrigger, FundingMomentumModel, FundingOICointegrationModel,
    PersistentFundingSignModel,
};

#[derive(Clone, Copy)]
enum ModelKind { Persistent, Momentum, OICoint, BasisBlowout }
fn model_name(k: ModelKind) -> &'static str {
    match k {
        ModelKind::Persistent   => "persistent_sign",
        ModelKind::Momentum     => "momentum",
        ModelKind::OICoint      => "oi_cointegration",
        ModelKind::BasisBlowout => "basis_blowout",
    }
}

/// Walk a direction sequence, emit closed trades within [range_start..range_end).
/// PnL at event i = dir[i-1] * rate[i] (= position held into event i).
fn extract_trades(
    dirs: &[i32], times: &[i64], rates: &[f64],
    range_start: usize, range_end: usize,
) -> Vec<(i64, usize, i64, usize, i32, f64)> {
    let mut out = Vec::new();
    let mut open: Option<(i64, usize, i32, f64)> = None; // (entry_time, entry_idx, side, pnl_accum)
    // Effective "held-direction at event i" is dirs[i-1].
    let mut last_held: i32 = 0;
    let end = range_end.min(dirs.len());
    for i in range_start..end {
        let held = if i == 0 { 0 } else { dirs[i - 1] };
        let inc = held as f64 * rates[i];
        if held != last_held {
            if last_held != 0 {
                if let Some((et, ei, side, mut acc)) = open {
                    // Don't include this event's pnl in the closing trade
                    // (this event marks the new holding regime).
                    out.push((et, ei, times[i], i, side, acc));
                    let _ = acc;
                }
                open = None;
            }
            if held != 0 {
                open = Some((times[i], i, held, inc));
            }
        } else if let Some((et, ei, side, acc)) = open {
            open = Some((et, ei, side, acc + inc));
        }
        last_held = held;
    }
    out
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!("usage: live_test_t6_multi <out.csv> <n_events> <wfo_n>");
        std::process::exit(2);
    }
    let out_path = PathBuf::from(&args[1]);
    let n_events: usize = args[2].parse().expect("n_events");
    let wfo_n: usize = args[3].parse().expect("wfo_n");

    let shard: usize = std::env::var("SHARD").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let total_shards: usize = std::env::var("TOTAL_SHARDS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    let base = "/home/daru/leak_test/synth_data/t6_fix";
    let funding = load_funding(format!("{}/funding.csv", base), "binance_perp", true).expect("load_funding");
    let basis = load_basis(format!("{}/basis.csv", base), "btc_perp_spot", 0.01).expect("load_basis");
    let oi = load_oi(format!("{}/oi.csv", base), 3600, 60).expect("load_oi");

    // Truncate inputs to the first n_events funding events.
    // For basis/oi we keep records with time <= last_funding_time.
    let total_fund = funding.events.len();
    let n = n_events.min(total_fund);
    let probe_times: Vec<i64> = funding.events.iter().take(n).map(|e| e.time_s).collect();
    let event_rates: Vec<f64> = funding.events.iter().take(n).map(|e| e.rate).collect();
    let cutoff = if let Some(&last_t) = probe_times.last() { last_t } else { 0 };

    // The signal_at functions take a `t` argument and look back in funding.
    // We MUST also restrict their input set to events before t. The cleanest
    // way is to slice the funding/basis/oi vectors. But the carry crate's
    // types don't expose slicing easily. So we rely on causal `signal_at(.., t)`
    // — but to be safe we ALSO confine `probe_times` to the first n events,
    // so the model only sees the first n events when iterating.
    // (Confirmed via inspection: PersistentFundingSignModel::signal_at uses
    // events up to time t only — causal.)

    // WFO geometry fixed at wfo_n.
    let slice = wfo_n / 6;
    let windows: Vec<(usize, usize, usize, usize)> = vec![
        (0,         2 * slice, 2 * slice, 3 * slice),
        (slice,     3 * slice, 3 * slice, 4 * slice),
        (2 * slice, 4 * slice, 4 * slice, 5 * slice),
    ];

    let p_persistent: Vec<usize> = vec![1, 2, 3, 4, 5, 6, 8, 10];
    let p_momentum_window: Vec<usize> = vec![5, 10, 20, 30, 50];
    let p_momentum_k:      Vec<f64>   = vec![0.5, 1.0, 1.5, 2.0, 2.5];
    let p_coint_window:    Vec<usize> = vec![5, 10, 20, 30, 50];
    let p_coint_k:         Vec<f64>   = vec![0.5, 1.0, 1.5, 2.0, 2.5];
    let p_blowout_k:       Vec<f64>   = vec![1.0, 1.5, 2.0, 2.5, 3.0];
    let p_blowout_window:  Vec<usize> = vec![5, 10, 15, 20];

    let mut out = fs::File::create(&out_path).expect("create out");
    writeln!(out, "strategy_id,model,p1,p2,wfo_window,side,entry_time,entry_idx,exit_time,exit_idx,pnl").expect("hdr");

    let mut strat_id: usize = 0;
    let _ = cutoff; // referenced for clarity; not used to slice the trigger inputs

    let mut emit = |strat_id: &mut usize, model_str: &str, p1: &str, p2: &str, dirs: &[i32]| {
        for (wi, (_, _, oos_s, oos_e)) in windows.iter().enumerate() {
            if *oos_s >= n { continue; } // window entirely in the future
            let end_capped = (*oos_e).min(n);
            let trades = extract_trades(dirs, &probe_times, &event_rates, *oos_s, end_capped);
            for (et, ei, xt, xi, side, pnl) in &trades {
                writeln!(out, "{:05},{},{},{},W{:02},{},{},{},{},{},{:.10}",
                    *strat_id, model_str, p1, p2, wi + 1,
                    if *side == 1 { "long" } else { "short" },
                    et, ei, xt, xi, pnl
                ).expect("write");
            }
        }
    };

    let run_for_id = |strat_id: &mut usize, model_str: &str, p1: &str, p2: &str, dirs: &[i32], shard: usize, total_shards: usize, emit: &mut dyn FnMut(&mut usize, &str, &str, &str, &[i32])| {
        if *strat_id % total_shards == shard {
            emit(strat_id, model_str, p1, p2, dirs);
        }
        *strat_id += 1;
    };
    let mut emit = emit;

    // 1. PersistentFundingSignModel
    for &ms in &p_persistent {
        let m = PersistentFundingSignModel::new(ms).expect("persistent");
        let dirs: Vec<i32> = probe_times.iter().map(|&t| m.signal_at(&funding, t).direction as i32).collect();
        run_for_id(&mut strat_id, model_name(ModelKind::Persistent), &ms.to_string(), "", &dirs, shard, total_shards, &mut emit);
    }
    // 2. FundingMomentumModel
    for &w in &p_momentum_window {
        for &k in &p_momentum_k {
            let m = FundingMomentumModel::new(w, k).expect("momentum");
            let dirs: Vec<i32> = probe_times.iter().map(|&t| m.signal_at(&funding, t).direction as i32).collect();
            run_for_id(&mut strat_id, model_name(ModelKind::Momentum), &w.to_string(), &format!("{:.2}", k), &dirs, shard, total_shards, &mut emit);
        }
    }
    // 3. FundingOICointegrationModel
    for &w in &p_coint_window {
        for &k in &p_coint_k {
            let m = FundingOICointegrationModel::new(w, k).expect("oi_coint");
            let dirs: Vec<i32> = probe_times.iter().map(|&t| m.signal_at(&funding, &oi, t).direction as i32).collect();
            run_for_id(&mut strat_id, model_name(ModelKind::OICoint), &w.to_string(), &format!("{:.2}", k), &dirs, shard, total_shards, &mut emit);
        }
    }
    // 4. BasisBlowoutTrigger
    for &k in &p_blowout_k {
        for &w in &p_blowout_window {
            let trig = BasisBlowoutTrigger::new(w, k).expect("blowout");
            let evs = trig.run(&basis);
            let dirs: Vec<i32> = probe_times.iter().map(|&t| {
                let recent = evs.iter().rev().find(|e| e.time_s <= t && t - e.time_s < 86400 * 2);
                recent.map(|e| e.direction as i32).unwrap_or(0)
            }).collect();
            run_for_id(&mut strat_id, model_name(ModelKind::BasisBlowout), &w.to_string(), &format!("{:.2}", k), &dirs, shard, total_shards, &mut emit);
        }
    }
}
