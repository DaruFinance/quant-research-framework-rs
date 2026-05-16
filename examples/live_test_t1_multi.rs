//! Multi-strategy live-test driver for T1 synth.
//!
//! Runs ALL T1 structural-product strategies (8 sigs × 5 tp × 4 mh × 3 size =
//! 480 per asset) on each of 3 synth asset CSVs, but ONLY processes the
//! prefix of length N given by `--n-bars`. IS is pinned to the first 10_000
//! bars (BACKTEST_CANDLES) via cfg.oos_candles = N - 10_000, so the
//! optimizer-picked LB per strategy is invariant across N. Output is one
//! combined trade ledger keyed by (asset, strategy_id, window, sample, side,
//! entry_time) — same schema as the engine's `trade_list.csv` plus the asset
//! tag and strategy_id prefix columns.
//!
//! Usage:
//!     live_test_t1_multi <out_trades.csv> <n_bars>
//!
//! Reads synth_A_eng.csv / synth_B_eng.csv / synth_C_eng.csv from
//! /home/daru/leak_test/synth_data/ and processes the first `n_bars` of each.

use std::collections::HashSet;
use std::fs;
use std::io::Write as _;
use std::path::PathBuf;

use quant_research_framework_rs::{
    classic_single_run, load_ohlc, Bar, Config, RawSignalsFn,
};

// ===========================================================================
// 8 signal families (verbatim from enumerate_t1_synth)
// ===========================================================================
fn shifted(sig: Vec<i8>) -> Vec<i8> {
    if sig.is_empty() { return sig; }
    let n = sig.len();
    let mut out = vec![0i8; n];
    out[0] = sig[0]; out[1..n].copy_from_slice(&sig[..n-1]); out
}
fn ema(close: &[f64], span: usize) -> Vec<f64> {
    let a = 2.0 / (span as f64 + 1.0); let mut out = vec![0.0; close.len()];
    if close.is_empty() { return out; } out[0] = close[0];
    for i in 1..close.len() { out[i] = a * close[i] + (1.0 - a) * out[i-1]; } out
}
fn sma(close: &[f64], length: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; close.len()]; let mut sum = 0.0;
    for i in 0..close.len() {
        sum += close[i];
        if i >= length { sum -= close[i-length]; }
        if i+1 >= length { out[i] = sum / length as f64; }
    } out
}
fn rsi(close: &[f64], length: usize) -> Vec<f64> {
    let n = close.len(); let mut out = vec![f64::NAN; n];
    if n < 2 { return out; }
    let a = 1.0 / length as f64; let mut g = 0.0f64; let mut l = 0.0f64;
    for i in 1..n {
        let d = close[i] - close[i-1];
        let gg = if d > 0.0 { d } else { 0.0 }; let ll = if d < 0.0 { -d } else { 0.0 };
        if i == 1 { g = gg; l = ll; } else { g = a*gg + (1.0-a)*g; l = a*ll + (1.0-a)*l; }
        if l <= 0.0 { out[i] = 100.0; } else { let rs = g/l; out[i] = 100.0 - 100.0/(1.0+rs); }
    } out
}
fn signal_sma_cross(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let f = sma(&c, lb); let s = sma(&c, lb*4); let mut sig = vec![0i8; b.len()];
    for i in 0..b.len() { if !f[i].is_nan() && !s[i].is_nan() {
        sig[i] = if f[i] > s[i] { 1 } else if f[i] < s[i] { -1 } else { 0 }; } }
    shifted(sig)
}
fn signal_ema_cross(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let f = ema(&c, lb); let s = ema(&c, lb*4); let mut sig = vec![0i8; b.len()];
    for i in 0..b.len() { sig[i] = if f[i] > s[i] { 1 } else if f[i] < s[i] { -1 } else { 0 }; }
    shifted(sig)
}
fn signal_macd_signal(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let fp = lb.max(2);
    let sp = ((lb as f64 * 2.16).round() as usize).max(fp+1);
    let sigp = ((lb as f64 * 0.75).round() as usize).clamp(2, 9);
    let fe = ema(&c, fp); let se = ema(&c, sp);
    let macd: Vec<f64> = fe.iter().zip(se.iter()).map(|(a,b)| a-b).collect();
    let sl = ema(&macd, sigp); let mut sig = vec![0i8; b.len()];
    for i in 0..b.len() { sig[i] = if macd[i] > sl[i] { 1 } else if macd[i] < sl[i] { -1 } else { 0 }; }
    shifted(sig)
}
fn signal_rsi_revert(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let r = rsi(&c, lb); let mut sig = vec![0i8; b.len()];
    for i in 0..b.len() { sig[i] = if !r[i].is_nan() && r[i] < 35.0 { 1 } else if !r[i].is_nan() && r[i] > 65.0 { -1 } else { 0 }; }
    shifted(sig)
}
fn signal_rsi_level(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let r = rsi(&c, lb); let mut sig = vec![0i8; b.len()];
    for i in 0..b.len() { sig[i] = if !r[i].is_nan() && r[i] > 55.0 { 1 } else if !r[i].is_nan() && r[i] < 45.0 { -1 } else { 0 }; }
    shifted(sig)
}
fn signal_donchian_breakout(b: &[Bar], lb: usize) -> Vec<i8> {
    let n = b.len(); let mut sig = vec![0i8; n]; if lb >= n { return sig; }
    for i in lb..n {
        let mut hi = f64::NEG_INFINITY; let mut lo = f64::INFINITY;
        for j in (i-lb)..i { if b[j].high > hi { hi = b[j].high; } if b[j].low < lo { lo = b[j].low; } }
        if b[i].close > hi { sig[i] = 1; } else if b[i].close < lo { sig[i] = -1; }
    }
    shifted(sig)
}
fn signal_stoch_k_cross(b: &[Bar], lb: usize) -> Vec<i8> {
    let n = b.len(); let mut k = vec![f64::NAN; n]; if lb >= n { return vec![0i8; n]; }
    for i in lb..n {
        let mut hi = f64::NEG_INFINITY; let mut lo = f64::INFINITY;
        for j in (i+1-lb)..=i { if b[j].high > hi { hi = b[j].high; } if b[j].low < lo { lo = b[j].low; } }
        if hi > lo { k[i] = 100.0 * (b[i].close - lo) / (hi - lo); }
    }
    let d = sma(&k, 3); let mut sig = vec![0i8; n];
    for i in 0..n { if !k[i].is_nan() && !d[i].is_nan() {
        sig[i] = if k[i] > d[i] && k[i] < 80.0 { 1 } else if k[i] < d[i] && k[i] > 20.0 { -1 } else { 0 }; } }
    shifted(sig)
}
fn signal_bb_revert(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect(); let n = b.len();
    let m = sma(&c, lb); let mut sd = vec![f64::NAN; n];
    for i in lb..n { let mean = m[i]; if !mean.is_nan() {
        let mut s2 = 0.0; for j in (i+1-lb)..=i { let d = c[j]-mean; s2 += d*d; }
        sd[i] = (s2 / lb as f64).sqrt(); } }
    let mut sig = vec![0i8; n];
    for i in 0..n { if !m[i].is_nan() && !sd[i].is_nan() {
        let u = m[i] + 2.0*sd[i]; let l = m[i] - 2.0*sd[i];
        sig[i] = if c[i] < l { 1 } else if c[i] > u { -1 } else { 0 }; } }
    shifted(sig)
}

#[derive(Clone, Copy)]
struct SigSpec { name: &'static str, f: RawSignalsFn }
fn signals() -> Vec<SigSpec> {
    vec![
        SigSpec { name: "sma_cross", f: signal_sma_cross },
        SigSpec { name: "ema_cross", f: signal_ema_cross },
        SigSpec { name: "macd_signal", f: signal_macd_signal },
        SigSpec { name: "rsi_revert", f: signal_rsi_revert },
        SigSpec { name: "rsi_level", f: signal_rsi_level },
        SigSpec { name: "donchian_breakout", f: signal_donchian_breakout },
        SigSpec { name: "stoch_k_cross", f: signal_stoch_k_cross },
        SigSpec { name: "bb_revert", f: signal_bb_revert },
    ]
}

#[derive(Clone)]
struct Asset { tag: &'static str, csv: &'static str }
fn assets() -> Vec<Asset> {
    vec![
        Asset { tag: "SYNTH_A", csv: "/home/daru/leak_test/synth_data/synth_A_eng.csv" },
        Asset { tag: "SYNTH_B", csv: "/home/daru/leak_test/synth_data/synth_B_eng.csv" },
        Asset { tag: "SYNTH_C", csv: "/home/daru/leak_test/synth_data/synth_C_eng.csv" },
    ]
}

fn silence_stdout<R, F: FnOnce() -> R>(f: F) -> R {
    use std::os::fd::AsRawFd;
    let saved = unsafe { libc::dup(1) };
    let devnull = fs::OpenOptions::new().write(true).open("/dev/null").expect("open /dev/null");
    unsafe { libc::dup2(devnull.as_raw_fd(), 1); }
    let r = f();
    unsafe { libc::dup2(saved, 1); libc::close(saved); }
    r
}

const IS_SIZE: usize = 10_000;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: live_test_t1_multi <out_trades.csv> <n_bars>");
        std::process::exit(2);
    }
    let out_path = PathBuf::from(&args[1]);
    let n_bars: usize = args[2].parse().expect("n_bars must be a positive integer");

    // Shard parallelism: a single process iterates a shard's strategies and
    // writes its own temp file; the Python driver concats them. Reuses the
    // pattern from enumerate_t1_synth.
    let shard: usize = std::env::var("SHARD").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let total_shards: usize = std::env::var("TOTAL_SHARDS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    let sigs = signals();
    let assets = assets();
    let tp_combos: Vec<(bool, f64)> = vec![(true,2.0),(true,3.0),(true,5.0),(true,8.0),(false,0.0)];
    let max_holds: Vec<usize> = vec![0, 24, 100, 500];
    let position_sizes: Vec<f64> = vec![1000.0, 2500.0, 5000.0];

    // Load each asset's prefix once.
    let mut bars_cache: Vec<(Asset, Vec<Bar>)> = Vec::new();
    for a in &assets {
        let mut bars = load_ohlc(a.csv);
        if bars.len() > n_bars { bars.truncate(n_bars); }
        bars_cache.push((a.clone(), bars));
    }

    // Engine emits trades to "trade_list.csv" in CWD; redirect by chdir'ing
    // to a per-shard subdir. We write our own combined output via append.
    let cwd = std::env::current_dir().expect("cwd");
    let scratch = cwd.join(format!("scratch_t1_s{}", shard));
    fs::create_dir_all(&scratch).expect("mkdir scratch");
    std::env::set_current_dir(&scratch).expect("chdir");

    // Open combined output. Header first.
    let mut out = fs::File::create(&out_path).expect("create out");
    writeln!(out, "asset,strategy_id,sig,tp_combo,use_tp,tp_pct,max_hold,position_size,engine_strategy,window,sample,side,entry_time,open_entry,high_entry,low_entry,close_entry,exit_time,open_exit,high_exit,low_exit,close_exit,pnl")
        .expect("write header");

    let mut strat_id: usize = 0;
    for (asset, bars) in &bars_cache {
        if bars.len() <= IS_SIZE {
            // Engine has no OOS bars yet for this asset; nothing to emit.
            strat_id += sigs.len() * tp_combos.len() * max_holds.len() * position_sizes.len();
            continue;
        }
        let oos_size = bars.len() - IS_SIZE;
        for sig in &sigs {
            for &(use_tp, tp_pct) in &tp_combos {
                for &mh in &max_holds {
                    for &ps in &position_sizes {
                        if strat_id % total_shards != shard {
                            strat_id += 1;
                            continue;
                        }
                        let mut cfg = Config::new();
                        cfg.tp_percentage = tp_pct;
                        cfg.use_tp = use_tp;
                        cfg.max_hold_bars = mh;
                        cfg.slippage_pct = 0.02;
                        cfg.fee_pct = 0.05;
                        cfg.position_size = ps;
                        cfg.oos_candles = oos_size;

                        // Run; engine writes trade_list.csv in CWD.
                        let _ = fs::remove_file("trade_list.csv");
                        silence_stdout(|| {
                            classic_single_run(bars, &mut cfg, sig.name, sig.f);
                        });

                        // Append trades to combined output with asset + strategy_id prefix.
                        if let Ok(s) = fs::read_to_string("trade_list.csv") {
                            for (i, line) in s.lines().enumerate() {
                                if i == 0 { continue; } // skip the engine's header
                                writeln!(out, "{},{:05},{},{},{},{:.3},{},{:.1},{}",
                                    asset.tag, strat_id, sig.name,
                                    if use_tp { "tp_on" } else { "tp_off" },
                                    use_tp, tp_pct, mh, ps, line
                                ).expect("write trade row");
                            }
                        }
                        strat_id += 1;
                    }
                }
            }
        }
    }
}
