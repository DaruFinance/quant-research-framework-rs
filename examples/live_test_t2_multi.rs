//! Multi-strategy live-test driver for T2 synth.
//!
//! Runs the full T2 enumeration on truncated synth A/B panel using a FIXED
//! WFO geometry (slice = wfo_n / 6, independent of actual data length).
//! Fixed WFO is required for live==batch trade-by-trade comparison: with
//! the production enumerator's data-dependent slicing, every live step's
//! WFO windows shift, so intermediate trades would never match batch.
//!
//! Output: one combined trade ledger keyed by
//!   (strategy_id, wfo_window, side, entry_time)
//! Each row also carries entry/exit indices, prices, and trade pnl. Open
//! positions at end-of-window are NOT emitted (mirrors live: an unclosed
//! position is not a "trade").
//!
//! Usage:
//!     live_test_t2_multi <out_trades.csv> <n_bars> <wfo_n>
//!
//! Reads synth_A_eng.csv and synth_B_eng.csv from /home/daru/leak_test/synth_data/

#![cfg(feature = "pairs")]

use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, Write as _};
use std::path::PathBuf;

use quant_research_framework_rs::pairs::{kalman_beta_spread, log_ratio, ols_resid};
use quant_research_framework_rs::panel::{load_panel, PanelData};

#[derive(Clone, Copy)]
enum SpreadMethod { LogRatio, OlsResid, Kalman }
fn spread_name(m: SpreadMethod) -> &'static str {
    match m {
        SpreadMethod::LogRatio => "log_ratio",
        SpreadMethod::OlsResid => "ols_resid",
        SpreadMethod::Kalman   => "kalman_beta",
    }
}
fn compute_spread(panel: &PanelData, method: SpreadMethod, lookback: usize) -> Vec<f64> {
    let n = panel.times.len();
    let t_idx = n - 1;
    match method {
        SpreadMethod::LogRatio => log_ratio(panel, "SYNTH_A", "SYNTH_B", t_idx).expect("log_ratio").spread,
        SpreadMethod::OlsResid => ols_resid(panel, "SYNTH_A", "SYNTH_B", t_idx, lookback).expect("ols_resid").spread,
        SpreadMethod::Kalman   => kalman_beta_spread(panel, "SYNTH_A", "SYNTH_B", t_idx, 1e-4, 1e-3).expect("kalman").spread,
    }
}
fn rolling_zscore(spread: &[f64], window: usize) -> Vec<f64> {
    let n = spread.len(); let mut z = vec![f64::NAN; n];
    if window == 0 || window > n { return z; }
    let mut sum = 0.0; let mut sumsq = 0.0;
    for i in 0..n {
        let v = spread[i];
        if v.is_finite() { sum += v; sumsq += v*v; }
        if i + 1 > window {
            let old = spread[i - window];
            if old.is_finite() { sum -= old; sumsq -= old*old; }
        }
        if i + 1 >= window {
            let mean = sum / window as f64;
            let var = (sumsq / window as f64 - mean*mean).max(0.0);
            let sd = var.sqrt();
            if sd > 1e-12 { z[i] = (v - mean) / sd; }
        }
    }
    z
}

const TAKER_FEE_PER_FILL: f64 = 0.0005;
const MAKER_FEE_PER_FILL: f64 = 0.0002;
const SLIP_PER_FILL:      f64 = 0.0002;
const FUNDING_PER_8H_PER_LEG: f64 = 0.0001;
const BARS_PER_FUNDING: usize = 16;

fn run_pairs(spread: &[f64], z: &[f64],
             entry_z: f64, exit_z: f64, stop_z: f64, max_hold: usize,
             range_start: usize, range_end: usize) -> (Vec<i8>, Vec<f64>, Vec<f64>) {
    let n = spread.len();
    let mut pos = vec![0i8; n];
    let mut pnl_inc = vec![0.0f64; n];
    let mut equity = vec![0.0f64; n];
    let mut bars_held: usize = 0;
    let mut last_pos: i8 = 0;
    let taker_leg = TAKER_FEE_PER_FILL + SLIP_PER_FILL;
    let maker_leg = MAKER_FEE_PER_FILL;
    let taker_close_pos = 2.0 * taker_leg;
    let maker_close_pos = 2.0 * maker_leg;
    let taker_open_pos  = 2.0 * taker_leg;
    let funding_cost    = 2.0 * FUNDING_PER_8H_PER_LEG;
    for i in (range_start.max(1))..range_end.min(n) {
        let zp = z[i - 1];
        let mut exit_reason: u8 = 0;
        let new_pos = if !zp.is_finite() { last_pos }
            else if last_pos == 0 {
                if zp > entry_z { -1 } else if zp < -entry_z { 1 } else { 0 }
            } else {
                let hold_break = max_hold > 0 && bars_held >= max_hold;
                let stop_break = zp.abs() > stop_z;
                let exit_break = zp.abs() < exit_z;
                if stop_break || exit_break { exit_reason = 1; 0 }
                else if hold_break          { exit_reason = 2; 0 }
                else                        { last_pos }
            };
        pos[i] = new_pos;
        let ds = spread[i] - spread[i - 1];
        let mut inc = if !ds.is_finite() { 0.0 } else { last_pos as f64 * ds };
        if last_pos != 0 && i % BARS_PER_FUNDING == 0 { inc -= funding_cost; }
        if new_pos != last_pos {
            if last_pos != 0 {
                inc -= match exit_reason { 1 => maker_close_pos, _ => taker_close_pos };
            }
            if new_pos != 0 { inc -= taker_open_pos; }
        }
        pnl_inc[i] = inc;
        equity[i] = if i > range_start { equity[i - 1] + inc } else { inc };
        bars_held = if new_pos != 0 && new_pos == last_pos { bars_held + 1 } else { 0 };
        last_pos = new_pos;
    }
    (pos, pnl_inc, equity)
}

/// Build the 2-asset panel from synth_A/B engine-format CSVs, optionally
/// truncated to `n_bars` rows.
fn build_panel(n_bars_cap: Option<usize>) -> PanelData {
    // Place temp panel files in CWD so each shard (different cwd) has its
    // own — avoids concurrent-write races on a shared /tmp path.
    let mut tmpdir = std::env::current_dir().expect("cwd"); tmpdir.push("t2_panel");
    fs::create_dir_all(&tmpdir).unwrap();
    let src_a = "/home/daru/leak_test/synth_data/synth_A_eng.csv";
    let src_b = "/home/daru/leak_test/synth_data/synth_B_eng.csv";
    fn read_ohlc(p: &str, cap: Option<usize>) -> BTreeMap<i64, [f64; 4]> {
        let f = std::io::BufReader::new(fs::File::open(p).expect("open"));
        let mut map: BTreeMap<i64, [f64; 4]> = BTreeMap::new();
        for (i, line) in f.lines().enumerate() {
            let line = line.unwrap();
            if i == 0 { continue; }
            if let Some(cap) = cap { if i > cap { break; } }
            let cols: Vec<&str> = line.split(',').collect();
            if cols.len() < 5 { continue; }
            let t: i64 = cols[0].parse().expect("ts");
            let o: f64 = cols[1].parse().unwrap();
            let h: f64 = cols[2].parse().unwrap();
            let l: f64 = cols[3].parse().unwrap();
            let c: f64 = cols[4].parse().unwrap();
            map.insert(t, [o, h, l, c]);
        }
        map
    }
    let a = read_ohlc(src_a, n_bars_cap);
    let b = read_ohlc(src_b, n_bars_cap);
    let common: Vec<i64> = a.keys().filter(|t| b.contains_key(t)).copied().collect();
    let a_path = tmpdir.join(format!("SYNTH_A_{}.csv", n_bars_cap.unwrap_or(0)));
    let b_path = tmpdir.join(format!("SYNTH_B_{}.csv", n_bars_cap.unwrap_or(0)));
    let mut aw = fs::File::create(&a_path).unwrap();
    let mut bw = fs::File::create(&b_path).unwrap();
    writeln!(aw, "time,open,high,low,close").unwrap();
    writeln!(bw, "time,open,high,low,close").unwrap();
    for &t in &common {
        let av = a[&t]; let bv = b[&t];
        writeln!(aw, "{},{},{},{},{}", t, av[0], av[1], av[2], av[3]).unwrap();
        writeln!(bw, "{},{},{},{},{}", t, bv[0], bv[1], bv[2], bv[3]).unwrap();
    }
    drop(aw); drop(bw);
    load_panel(&[
        ("SYNTH_A".to_string(), a_path.clone()),
        ("SYNTH_B".to_string(), b_path.clone()),
    ]).expect("load_panel")
}

/// Extract closed trades from a position series.
///
/// Returns Vec<(entry_time, entry_idx, exit_time, exit_idx, side, pnl_sum)>
/// for each entry→exit cycle within [range_start..range_end). Open positions
/// at range_end are NOT emitted.
fn extract_trades(
    pos: &[i8], pnl_inc: &[f64], times: &[i64],
    range_start: usize, range_end: usize,
) -> Vec<(i64, usize, i64, usize, i8, f64)> {
    let mut out = Vec::new();
    let mut open: Option<(i64, usize, i8)> = None; // (entry_time, entry_idx, side)
    let mut accumulated_pnl = 0.0f64;
    let mut last_p: i8 = 0;
    let end = range_end.min(pos.len());
    for i in range_start..end {
        let p = pos[i];
        if i > range_start { accumulated_pnl += pnl_inc[i]; }
        if p != last_p {
            if last_p != 0 {
                if let Some((et, ei, side)) = open {
                    out.push((et, ei, times[i], i, side, accumulated_pnl));
                }
                open = None;
                accumulated_pnl = 0.0;
            }
            if p != 0 {
                open = Some((times[i], i, p));
                accumulated_pnl = pnl_inc[i];
            }
        }
        last_p = p;
    }
    out
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!("usage: live_test_t2_multi <out_trades.csv> <n_bars> <wfo_n>");
        std::process::exit(2);
    }
    let out_path = PathBuf::from(&args[1]);
    let n_bars: usize = args[2].parse().expect("n_bars");
    let wfo_n: usize = args[3].parse().expect("wfo_n");

    let shard: usize = std::env::var("SHARD").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let total_shards: usize = std::env::var("TOTAL_SHARDS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    let panel = build_panel(Some(n_bars));
    let n = panel.times.len();

    // WFO geometry — FIXED across live invocations (based on wfo_n, not n).
    let slice = wfo_n / 6;
    let windows: Vec<(usize, usize, usize, usize)> = vec![
        (0,         2 * slice, 2 * slice, 3 * slice),
        (slice,     3 * slice, 3 * slice, 4 * slice),
        (2 * slice, 4 * slice, 4 * slice, 5 * slice),
    ];

    let methods = [SpreadMethod::LogRatio, SpreadMethod::OlsResid, SpreadMethod::Kalman];
    let lookbacks: Vec<usize> = vec![60, 120, 240];
    let z_windows: Vec<usize> = vec![60, 120, 240];
    let entry_zs: Vec<f64> = vec![1.0, 1.5, 2.0, 2.5, 3.0];
    let exit_zs:  Vec<f64> = vec![0.0, 0.5, 1.0];
    let stop_zs:  Vec<f64> = vec![3.0, 4.0, 5.0];
    let max_holds: Vec<usize> = vec![0, 100, 500];

    let mut spread_cache: std::collections::HashMap<(usize, usize), Vec<f64>> = Default::default();
    for (mi, m) in methods.iter().enumerate() {
        let lbs: Vec<usize> = match m { SpreadMethod::OlsResid => lookbacks.clone(), _ => vec![0] };
        for &lb in &lbs { spread_cache.insert((mi, lb), compute_spread(&panel, *m, lb)); }
    }

    let mut out = fs::File::create(&out_path).expect("create out");
    writeln!(out, "strategy_id,method,lookback,z_window,entry_z,exit_z,stop_z,max_hold,wfo_window,side,entry_time,entry_idx,exit_time,exit_idx,pnl").expect("hdr");

    let mut strat_id: usize = 0;
    for (mi, m) in methods.iter().enumerate() {
        let lbs: Vec<usize> = match m { SpreadMethod::OlsResid => lookbacks.clone(), _ => vec![0] };
        for &lb in &lbs {
            let spread = spread_cache.get(&(mi, lb)).unwrap().clone();
            for &zw in &z_windows {
                let z = rolling_zscore(&spread, zw);
                for &entry_z in &entry_zs {
                    for &exit_z in &exit_zs {
                        if exit_z >= entry_z { continue; }
                        for &stop_z in &stop_zs {
                            if stop_z <= entry_z { continue; }
                            for &mh in &max_holds {
                                if strat_id % total_shards != shard {
                                    strat_id += 1; continue;
                                }
                                // For each WFO window, cap range_end to current n.
                                // run_pairs internally also does range_end.min(n).
                                for (wi, (_is_s, _is_e, oos_s, oos_e)) in windows.iter().enumerate() {
                                    if *oos_s >= n { continue; } // window entirely future
                                    let end_capped = (*oos_e).min(n);
                                    let (pos, pnl_inc, _eq) = run_pairs(
                                        &spread, &z, entry_z, exit_z, stop_z, mh,
                                        *oos_s, end_capped,
                                    );
                                    let trades = extract_trades(&pos, &pnl_inc, &panel.times, *oos_s, end_capped);
                                    for (et, ei, xt, xi, side, pnl) in &trades {
                                        writeln!(out, "{:05},{},{},{},{:.1},{:.1},{:.1},{},W{:02},{},{},{},{},{},{:.10}",
                                            strat_id, spread_name(*m), lb, zw,
                                            entry_z, exit_z, stop_z, mh,
                                            wi + 1,
                                            if *side == 1 { "long" } else { "short" },
                                            et, ei, xt, xi, pnl
                                        ).expect("write");
                                    }
                                }
                                strat_id += 1;
                            }
                        }
                    }
                }
            }
        }
    }
}
