//! T2 (Mean-Reversion / Pairs / Stat-Arb) structural enumerator with WFO.
//!
//! Uses BTC-DOGE 30m crypto data (intersection of timestamps), runs each
//! strategy across 3 sliding WFO windows. Per-strategy axes:
//! (spread method × ols-lookback × z-window × entry_z × exit_z × stop_z
//!  × max_hold). Each strategy emits a per-bar pnl + equity series with
//! window membership in the CSV.
//!
//! Run with:
//!     cargo run --release --jobs 1 --features pairs --example enumerate_t2

#![cfg(feature = "pairs")]

use std::collections::BTreeMap;
use std::fs;
use std::io::Write as _;
use std::path::PathBuf;
use std::time::Instant;

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
        SpreadMethod::LogRatio => log_ratio(panel, "BTC", "DOGE", t_idx).expect("log_ratio").spread,
        SpreadMethod::OlsResid => ols_resid(panel, "BTC", "DOGE", t_idx, lookback).expect("ols_resid").spread,
        SpreadMethod::Kalman   => kalman_beta_spread(panel, "BTC", "DOGE", t_idx, 1e-4, 1e-3).expect("kalman").spread,
    }
}

/// Causal rolling z-score; z[i] uses spread[i+1-window..=i].
fn rolling_zscore(spread: &[f64], window: usize) -> Vec<f64> {
    let n = spread.len();
    let mut z = vec![f64::NAN; n];
    if window == 0 || window > n { return z; }
    let mut sum = 0.0; let mut sumsq = 0.0;
    for i in 0..n {
        let v = spread[i];
        if v.is_finite() { sum += v; sumsq += v * v; }
        if i + 1 > window {
            let old = spread[i - window];
            if old.is_finite() { sum -= old; sumsq -= old * old; }
        }
        if i + 1 >= window {
            let mean = sum / window as f64;
            let var = (sumsq / window as f64 - mean * mean).max(0.0);
            let sd = var.sqrt();
            if sd > 1e-12 { z[i] = (v - mean) / sd; }
        }
    }
    z
}

// Cost model for the BTC-DOGE pair on perpetuals.
//   Entries always TAKER (market in on signal):     0.05% fee + 0.02% slip = 0.07%
//   Exit on TP (|z|<exit_z) or SL (|z|>stop_z):     MAKER (resting limit):  0.02% fee, 0% slip
//   Exit on max_hold (time-out): TAKER (market out): 0.07%
//   Funding: 0.01% per 8h per leg → every 16 bars.
const TAKER_FEE_PER_FILL: f64 = 0.0005;   // 0.05% taker
const MAKER_FEE_PER_FILL: f64 = 0.0002;   // 0.02% maker
const SLIP_PER_FILL:      f64 = 0.0002;   // 0.02% slippage (taker only)
const FUNDING_PER_8H_PER_LEG: f64 = 0.0001;  // 0.01% per 8h per leg
const BARS_PER_FUNDING: usize = 16;          // 30m bars × 16 = 8 h

/// Causal pairs strategy with realistic perp costs.
///
/// Decision at bar i uses z[i-1]; resulting position pos[i] is established
/// at bar i but only earns PnL starting at bar i+1 (no bar-of-entry PnL).
///
/// Costs deducted from pnl_inc:
///   - entry: 2 legs × (fee + slip) = 0.08% applied at the bar of entry
///   - exit:  2 legs × (fee + slip) = 0.08% applied at the bar of exit
///   - reversal (entry+exit same bar): 0.16%
///   - funding: 2 legs × 0.01% every 16 bars while holding
fn run_pairs(spread: &[f64], z: &[f64],
             entry_z: f64, exit_z: f64, stop_z: f64, max_hold: usize,
             range_start: usize, range_end: usize) -> (Vec<i8>, Vec<f64>, Vec<f64>) {
    let n = spread.len();
    let mut pos = vec![0i8; n];
    let mut pnl_inc = vec![0.0f64; n];
    let mut equity = vec![0.0f64; n];
    let mut bars_held: usize = 0;
    let mut last_pos: i8 = 0;

    let taker_leg = TAKER_FEE_PER_FILL + SLIP_PER_FILL;     // 0.07% per leg per taker fill
    let maker_leg = MAKER_FEE_PER_FILL;                      // 0.02% per leg per maker fill
    let taker_close_pos = 2.0 * taker_leg;                   // 0.14% close both legs taker
    let maker_close_pos = 2.0 * maker_leg;                   // 0.04% close both legs maker (SL/TP)
    let taker_open_pos  = 2.0 * taker_leg;                   // 0.14% open both legs taker
    let funding_cost    = 2.0 * FUNDING_PER_8H_PER_LEG;      // 0.02% per funding event

    for i in (range_start.max(1))..range_end.min(n) {
        let zp = z[i - 1];
        // Decide exit reason BEFORE computing new_pos so we can choose
        // maker (SL/TP) vs taker (max_hold time-out) close fee.
        let mut exit_reason: u8 = 0;   // 0 = no exit, 1 = TP/SL maker, 2 = max_hold taker
        let new_pos = if !zp.is_finite() {
            last_pos
        } else if last_pos == 0 {
            if zp > entry_z { -1 }
            else if zp < -entry_z { 1 }
            else { 0 }
        } else {
            let hold_break = max_hold > 0 && bars_held >= max_hold;
            let stop_break = zp.abs() > stop_z;     // SL: |z| > stop_z
            let exit_break = zp.abs() < exit_z;     // TP: |z| < exit_z (mean reverted)
            if stop_break || exit_break { exit_reason = 1; 0 }
            else if hold_break          { exit_reason = 2; 0 }
            else                        { last_pos }
        };
        pos[i] = new_pos;

        // Gross PnL: position we were HOLDING during the i-1 → i move.
        let ds = spread[i] - spread[i - 1];
        let mut inc = if !ds.is_finite() { 0.0 } else { last_pos as f64 * ds };

        // Funding: pay every 16 bars while holding.
        if last_pos != 0 && i % BARS_PER_FUNDING == 0 {
            inc -= funding_cost;
        }

        // Trade costs.
        if new_pos != last_pos {
            // Closing the existing position.
            if last_pos != 0 {
                inc -= match exit_reason {
                    1 => maker_close_pos,   // SL or TP hit → resting limit → maker
                    _ => taker_close_pos,   // max_hold or any other → market out → taker
                };
            }
            // Opening a new position is always TAKER (market in on signal).
            if new_pos != 0 {
                inc -= taker_open_pos;
            }
        }

        pnl_inc[i] = inc;
        equity[i] = if i > range_start { equity[i - 1] + inc } else { inc };
        bars_held = if new_pos != 0 && new_pos == last_pos { bars_held + 1 } else { 0 };
        last_pos = new_pos;
    }
    (pos, pnl_inc, equity)
}

/// Build a 2-asset panel from BTCUSDT_30m.csv and DOGEUSDT_30m.csv,
/// keeping only rows present at common timestamps.
fn build_btc_doge_panel() -> PanelData {
    use std::io::BufRead;
    let mut tmpdir = std::env::temp_dir(); tmpdir.push("t2_btc_doge");
    fs::create_dir_all(&tmpdir).unwrap();
    let src_btc  = "/home/daru/quant-research-framework-rs-v2/data/BTCUSDT_30m.csv";
    let src_doge = "/home/daru/quant-research-framework-rs-v2/data/DOGEUSDT_30m.csv";

    fn read_ohlc(p: &str) -> BTreeMap<i64, [f64; 4]> {
        let f = std::io::BufReader::new(fs::File::open(p).expect("open"));
        let mut map: BTreeMap<i64, [f64; 4]> = BTreeMap::new();
        for (i, line) in f.lines().enumerate() {
            let line = line.unwrap();
            if i == 0 { continue; }
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

    let btc = read_ohlc(src_btc);
    let doge = read_ohlc(src_doge);
    let common: Vec<i64> = btc.keys().filter(|t| doge.contains_key(t)).copied().collect();
    eprintln!("BTC bars: {} | DOGE bars: {} | common: {}", btc.len(), doge.len(), common.len());

    // Find the longest contiguous run with the expected 1800s (30 min) modal delta.
    // The panel loader rejects any gap > modal delta, so we restrict to a clean run.
    let modal: i64 = 1800;
    let mut best_start = 0usize; let mut best_len = 0usize;
    let mut run_start = 0usize;
    for i in 1..common.len() {
        let dt = common[i] - common[i - 1];
        if dt != modal {
            let len = i - run_start;
            if len > best_len { best_len = len; best_start = run_start; }
            run_start = i;
        }
    }
    let last_len = common.len() - run_start;
    if last_len > best_len { best_len = last_len; best_start = run_start; }
    let common: Vec<i64> = common[best_start..best_start + best_len].to_vec();
    eprintln!("Longest contiguous 30m run: {} bars (from ts={})",
        common.len(), common.first().copied().unwrap_or(0));

    let btc_path  = tmpdir.join("BTC.csv");
    let doge_path = tmpdir.join("DOGE.csv");
    let mut bw = fs::File::create(&btc_path).unwrap();
    let mut dw = fs::File::create(&doge_path).unwrap();
    writeln!(bw, "time,open,high,low,close").unwrap();
    writeln!(dw, "time,open,high,low,close").unwrap();
    for &t in &common {
        let b = btc[&t]; let d = doge[&t];
        writeln!(bw, "{},{},{},{},{}", t, b[0], b[1], b[2], b[3]).unwrap();
        writeln!(dw, "{},{},{},{},{}", t, d[0], d[1], d[2], d[3]).unwrap();
    }
    drop(bw); drop(dw);
    load_panel(&[
        ("BTC".to_string(),  btc_path.clone()),
        ("DOGE".to_string(), doge_path.clone()),
    ]).expect("load_panel")
}

fn main() {
    // Sharded execution: each worker handles strategy_id % TOTAL_SHARDS == SHARD.
    let shard: usize = std::env::var("SHARD").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let total_shards: usize = std::env::var("TOTAL_SHARDS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    let panel = build_btc_doge_panel();
    let n = panel.times.len();
    eprintln!("[shard {}/{}] loaded BTC-DOGE 30m panel: {} aligned bars", shard, total_shards, n);

    // 3-window WFO geometry: [IS₁ → OOS₁ → IS₂ → OOS₂ → IS₃ → OOS₃]
    // Window placement: 6 equal slices. IS = 2 slices, OOS = 1 slice (rolling).
    let slice = n / 6;
    let windows: Vec<(usize, usize, usize, usize)> = vec![
        // (is_start, is_end, oos_start, oos_end)
        (0,         2 * slice, 2 * slice, 3 * slice),
        (slice,     3 * slice, 3 * slice, 4 * slice),
        (2 * slice, 4 * slice, 4 * slice, 5 * slice),
    ];
    eprintln!("WFO geometry (slice={} bars):", slice);
    for (i, w) in windows.iter().enumerate() {
        eprintln!("  W{:02}: IS [{}..{}]  OOS [{}..{}]", i + 1, w.0, w.1, w.2, w.3);
    }

    let out_root = PathBuf::from("/home/daru/strategies/T2");
    fs::create_dir_all(&out_root).unwrap();
    let index_path = out_root.join(format!("INDEX_shard{}.txt", shard));
    let mut index = fs::File::create(&index_path).unwrap();
    writeln!(index, "# T2 enumeration shard {}/{} — id,method,lookback,z_window,entry_z,exit_z,stop_z,max_hold,path,oos_pnl_W1,oos_pnl_W2,oos_pnl_W3,oos_pnl_total,n_oos_trades,elapsed_s",
        shard, total_shards).unwrap();

    let methods = [SpreadMethod::LogRatio, SpreadMethod::OlsResid, SpreadMethod::Kalman];
    let lookbacks: Vec<usize> = vec![60, 120, 240];
    let z_windows: Vec<usize> = vec![60, 120, 240];
    let entry_zs: Vec<f64> = vec![1.0, 1.5, 2.0, 2.5, 3.0];
    let exit_zs:  Vec<f64> = vec![0.0, 0.5, 1.0];
    let stop_zs:  Vec<f64> = vec![3.0, 4.0, 5.0];
    let max_holds: Vec<usize> = vec![0, 100, 500];

    // Precompute spread series per (method, lookback).
    let mut spread_cache: std::collections::HashMap<(usize, usize), Vec<f64>> = Default::default();
    for (mi, m) in methods.iter().enumerate() {
        let lbs: Vec<usize> = match m {
            SpreadMethod::OlsResid => lookbacks.clone(),
            _ => vec![0],
        };
        for &lb in &lbs {
            let s = compute_spread(&panel, *m, lb);
            spread_cache.insert((mi, lb), s);
        }
    }
    eprintln!("precomputed {} spread series", spread_cache.len());

    let total_target = methods.len() * lookbacks.len() * z_windows.len()
                       * entry_zs.len() * exit_zs.len() * stop_zs.len() * max_holds.len();
    eprintln!("T2 enumeration target (pre-filter): {}", total_target);

    let mut strat_id: usize = 0;
    let t_start = Instant::now();

    for (mi, m) in methods.iter().enumerate() {
        let lbs: Vec<usize> = match m {
            SpreadMethod::OlsResid => lookbacks.clone(),
            _ => vec![0],
        };
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
                                // Shard filter
                                if strat_id % total_shards != shard {
                                    strat_id += 1;
                                    continue;
                                }
                                let t0 = Instant::now();
                                // Run each WFO window's OOS, concatenate.
                                let mut pos_full = vec![0i8; n];
                                let mut pnl_full = vec![0.0f64; n];
                                let mut equity_full = vec![0.0f64; n];
                                let mut window_id = vec![0u8; n];
                                let mut oos_pnls = [0.0f64; 3];
                                let mut n_oos_trades = 0usize;
                                let mut prev_eq = 0.0f64;
                                for (wi, (is_s, is_e, oos_s, oos_e)) in windows.iter().enumerate() {
                                    let _is = (is_s, is_e); // engine has no IS-opt for pairs (no [IS] params today)
                                    let (pos, pnl, _eq) = run_pairs(&spread, &z, entry_z, exit_z, stop_z, mh, *oos_s, *oos_e);
                                    let mut last_p: i8 = 0;
                                    for i in *oos_s..*oos_e {
                                        pos_full[i] = pos[i];
                                        pnl_full[i] = pnl[i];
                                        prev_eq += pnl[i];
                                        equity_full[i] = prev_eq;
                                        window_id[i] = (wi + 1) as u8;
                                        if pos[i] != last_p && last_p != 0 { n_oos_trades += 1; }
                                        oos_pnls[wi] += pnl[i];
                                        last_p = pos[i];
                                    }
                                }
                                let elapsed = t0.elapsed().as_secs_f64();
                                let out_path = out_root.join(format!("t2_{:05}.csv", strat_id));
                                let mut f = fs::File::create(&out_path).unwrap();
                                writeln!(f, "i,time,wfo_window,spread,z,position,pnl_inc,equity").unwrap();
                                for i in 0..n {
                                    writeln!(f, "{},{},{},{},{},{},{},{}",
                                        i, panel.times[i], window_id[i],
                                        spread[i], z[i], pos_full[i], pnl_full[i], equity_full[i]
                                    ).unwrap();
                                }
                                let total_oos: f64 = oos_pnls.iter().sum();
                                writeln!(index,
                                    "{:05},{},{},{},{:.1},{:.1},{:.1},{},{},{:.6},{:.6},{:.6},{:.6},{},{:.4}",
                                    strat_id, spread_name(*m), lb, zw,
                                    entry_z, exit_z, stop_z, mh,
                                    out_path.display(),
                                    oos_pnls[0], oos_pnls[1], oos_pnls[2], total_oos,
                                    n_oos_trades, elapsed
                                ).unwrap();

                                if strat_id % 200 == 0 {
                                    eprintln!("  {:05} {} lb={} zw={} entry={:.1} mh={} oos_total={:.4}",
                                        strat_id, spread_name(*m), lb, zw, entry_z, mh, total_oos);
                                }
                                strat_id += 1;
                            }
                        }
                    }
                }
            }
        }
    }
    eprintln!("DONE T2 — {} strategies in {:.1}s", strat_id, t_start.elapsed().as_secs_f64());
}
