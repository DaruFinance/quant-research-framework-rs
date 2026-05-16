//! T1 (Trend/Momentum) structural enumerator — SYNTHETIC-DATA variant.
//!
//! Identical structural product to `enumerate_t1.rs`, but consumes three
//! synthetic 100k-bar GBM CSVs (seeds 11/22/33) instead of the real
//! SOL/BTC/DOGE fixtures. Output goes to
//! `/home/daru/leak_test/strategies/T1_synth/` so the real-data Phase B
//! output at `/home/daru/strategies/T1/` is untouched.
//!
//! Run with:
//!     cargo run --release --jobs 1 --example enumerate_t1_synth

use std::fs;
use std::io::Write as _;
use std::path::PathBuf;
use std::time::Instant;

use quant_research_framework_rs::{
    load_ohlc, run_cfg, Bar, Config, RawSignalsFn,
};

// ===========================================================================
// 8 signal families (structural [B] axis) — copied verbatim from enumerate_t1
// ===========================================================================

fn shifted(sig: Vec<i8>) -> Vec<i8> {
    if sig.is_empty() { return sig; }
    let n = sig.len();
    let mut out = vec![0i8; n];
    out[0] = sig[0];
    out[1..n].copy_from_slice(&sig[..n - 1]);
    out
}

fn ema(close: &[f64], span: usize) -> Vec<f64> {
    let alpha = 2.0 / (span as f64 + 1.0);
    let mut out = vec![0.0; close.len()];
    if close.is_empty() { return out; }
    out[0] = close[0];
    for i in 1..close.len() {
        out[i] = alpha * close[i] + (1.0 - alpha) * out[i - 1];
    }
    out
}

fn sma(close: &[f64], length: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; close.len()];
    let mut sum = 0.0;
    for i in 0..close.len() {
        sum += close[i];
        if i >= length { sum -= close[i - length]; }
        if i + 1 >= length { out[i] = sum / length as f64; }
    }
    out
}

fn rsi(close: &[f64], length: usize) -> Vec<f64> {
    let n = close.len();
    let mut out = vec![f64::NAN; n];
    if n < 2 { return out; }
    let alpha = 1.0 / length as f64;
    let mut avg_gain = 0.0f64;
    let mut avg_loss = 0.0f64;
    for i in 1..n {
        let d = close[i] - close[i - 1];
        let g = if d > 0.0 { d } else { 0.0 };
        let l = if d < 0.0 { -d } else { 0.0 };
        if i == 1 { avg_gain = g; avg_loss = l; }
        else { avg_gain = alpha * g + (1.0 - alpha) * avg_gain;
               avg_loss = alpha * l + (1.0 - alpha) * avg_loss; }
        if avg_loss <= 0.0 { out[i] = 100.0; }
        else { let rs = avg_gain / avg_loss; out[i] = 100.0 - 100.0 / (1.0 + rs); }
    }
    out
}

fn signal_sma_cross(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = sma(&close, lb);
    let slow = sma(&close, lb * 4);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        if !fast[i].is_nan() && !slow[i].is_nan() {
            sig[i] = if fast[i] > slow[i] { 1 } else if fast[i] < slow[i] { -1 } else { 0 };
        }
    }
    shifted(sig)
}

fn signal_ema_cross(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = ema(&close, lb);
    let slow = ema(&close, lb * 4);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if fast[i] > slow[i] { 1 } else if fast[i] < slow[i] { -1 } else { 0 };
    }
    shifted(sig)
}

fn signal_macd_signal(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast_p = lb.max(2);
    let slow_p = ((lb as f64 * 2.16).round() as usize).max(fast_p + 1);
    let sig_p = ((lb as f64 * 0.75).round() as usize).clamp(2, 9);
    let fe = ema(&close, fast_p);
    let se = ema(&close, slow_p);
    let macd: Vec<f64> = fe.iter().zip(se.iter()).map(|(a, b)| a - b).collect();
    let sig_line = ema(&macd, sig_p);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if macd[i] > sig_line[i] { 1 }
                 else if macd[i] < sig_line[i] { -1 } else { 0 };
    }
    shifted(sig)
}

fn signal_rsi_revert(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let r = rsi(&close, lb);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if !r[i].is_nan() && r[i] < 35.0 { 1 }
                 else if !r[i].is_nan() && r[i] > 65.0 { -1 } else { 0 };
    }
    shifted(sig)
}

fn signal_rsi_level(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let r = rsi(&close, lb);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if !r[i].is_nan() && r[i] > 55.0 { 1 }
                 else if !r[i].is_nan() && r[i] < 45.0 { -1 } else { 0 };
    }
    shifted(sig)
}

fn signal_donchian_breakout(bars: &[Bar], lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut sig = vec![0i8; n];
    if lb >= n { return sig; }
    for i in lb..n {
        let mut hi = f64::NEG_INFINITY; let mut lo = f64::INFINITY;
        for j in (i - lb)..i {
            if bars[j].high > hi { hi = bars[j].high; }
            if bars[j].low  < lo { lo = bars[j].low;  }
        }
        if bars[i].close > hi { sig[i] = 1; }
        else if bars[i].close < lo { sig[i] = -1; }
    }
    shifted(sig)
}

fn signal_stoch_k_cross(bars: &[Bar], lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut k = vec![f64::NAN; n];
    if lb >= n { return vec![0i8; n]; }
    for i in lb..n {
        let mut hi = f64::NEG_INFINITY; let mut lo = f64::INFINITY;
        for j in (i + 1 - lb)..=i {
            if bars[j].high > hi { hi = bars[j].high; }
            if bars[j].low  < lo { lo = bars[j].low;  }
        }
        if hi > lo { k[i] = 100.0 * (bars[i].close - lo) / (hi - lo); }
    }
    let d = sma(&k, 3);
    let mut sig = vec![0i8; n];
    for i in 0..n {
        if !k[i].is_nan() && !d[i].is_nan() {
            sig[i] = if k[i] > d[i] && k[i] < 80.0 { 1 }
                     else if k[i] < d[i] && k[i] > 20.0 { -1 } else { 0 };
        }
    }
    shifted(sig)
}

fn signal_bb_revert(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let n = bars.len();
    let m = sma(&close, lb);
    let mut sd = vec![f64::NAN; n];
    for i in lb..n {
        let mean = m[i];
        if !mean.is_nan() {
            let mut s2 = 0.0;
            for j in (i + 1 - lb)..=i { let d = close[j] - mean; s2 += d * d; }
            sd[i] = (s2 / lb as f64).sqrt();
        }
    }
    let mut sig = vec![0i8; n];
    for i in 0..n {
        if !m[i].is_nan() && !sd[i].is_nan() {
            let upper = m[i] + 2.0 * sd[i];
            let lower = m[i] - 2.0 * sd[i];
            sig[i] = if close[i] < lower { 1 }
                     else if close[i] > upper { -1 } else { 0 };
        }
    }
    shifted(sig)
}

#[derive(Clone, Copy)]
struct SigSpec { name: &'static str, f: RawSignalsFn }

fn signals() -> Vec<SigSpec> {
    vec![
        SigSpec { name: "sma_cross",         f: signal_sma_cross },
        SigSpec { name: "ema_cross",         f: signal_ema_cross },
        SigSpec { name: "macd_signal",       f: signal_macd_signal },
        SigSpec { name: "rsi_revert",        f: signal_rsi_revert },
        SigSpec { name: "rsi_level",         f: signal_rsi_level },
        SigSpec { name: "donchian_breakout", f: signal_donchian_breakout },
        SigSpec { name: "stoch_k_cross",     f: signal_stoch_k_cross },
        SigSpec { name: "bb_revert",         f: signal_bb_revert },
    ]
}

#[derive(Clone)]
struct Asset { tag: &'static str, csv: &'static str }

fn assets() -> Vec<Asset> {
    // SYNTHETIC: three independent GBM streams, 100k bars each, seeds 11/22/33.
    // Engine-format (no asset col): time,open,high,low,close.
    vec![
        Asset { tag: "SYNTH_A", csv: "/home/daru/leak_test/synth_data/synth_A_eng.csv" },
        Asset { tag: "SYNTH_B", csv: "/home/daru/leak_test/synth_data/synth_B_eng.csv" },
        Asset { tag: "SYNTH_C", csv: "/home/daru/leak_test/synth_data/synth_C_eng.csv" },
    ]
}

fn main() {
    let shard: usize = std::env::var("SHARD").ok().and_then(|s| s.parse().ok()).unwrap_or(0);
    let total_shards: usize = std::env::var("TOTAL_SHARDS").ok().and_then(|s| s.parse().ok()).unwrap_or(1);

    // SYNTH output root — separate from real-data Phase B output.
    let out_root = PathBuf::from("/home/daru/leak_test/strategies/T1_synth");
    fs::create_dir_all(&out_root).expect("mkdir T1_synth");
    let index_path = out_root.join(format!("INDEX_shard{}.txt", shard));
    let mut index = fs::File::create(&index_path).expect("INDEX shard");
    writeln!(index, "# T1 SYNTH enumeration shard {}/{} — strategy_id,sig,tp_combo,use_tp,tp_pct,max_hold,slip_pct,position_size,asset,path,n_rows,n_wfo_windows,oos_pnl_sum",
        shard, total_shards).unwrap();

    let sigs = signals();
    let assets = assets();
    let tp_combos: Vec<(bool, f64)> = vec![
        (true, 2.0), (true, 3.0), (true, 5.0), (true, 8.0), (false, 0.0),
    ];
    let max_holds: Vec<usize> = vec![0, 24, 100, 500];
    let slip_pcts: Vec<f64> = vec![0.02];
    let position_sizes: Vec<f64> = vec![1000.0, 2500.0, 5000.0];

    let total_count = sigs.len() * assets.len() * tp_combos.len()
                      * max_holds.len() * slip_pcts.len() * position_sizes.len();
    eprintln!("T1 SYNTH enumeration shard {}/{}: total={} strategies", shard, total_shards, total_count);

    let mut strat_id: usize = 0;
    let t_start = Instant::now();

    let mut bars_cache: Vec<(Asset, Vec<Bar>)> = Vec::new();
    for a in &assets {
        let bars = load_ohlc(a.csv);
        eprintln!("loaded {}: {} bars", a.tag, bars.len());
        bars_cache.push((a.clone(), bars));
    }

    for (asset, bars) in &bars_cache {
        for sig in &sigs {
            for &(use_tp, tp_pct) in &tp_combos {
                for &mh in &max_holds {
                    for &slip in &slip_pcts {
                        for &ps in &position_sizes {
                            if strat_id % total_shards != shard {
                                strat_id += 1;
                                continue;
                            }
                            let mut cfg = Config::new();
                            cfg.tp_percentage = tp_pct;
                            cfg.use_tp = use_tp;
                            cfg.max_hold_bars = mh;
                            cfg.slippage_pct = slip;
                            cfg.fee_pct = 0.05;
                            cfg.position_size = ps;

                            let t0 = Instant::now();
                            silence_stdout(|| {
                                run_cfg(bars, sig.name, sig.f, cfg.clone());
                            });
                            let elapsed = t0.elapsed().as_secs_f64();

                            let out_path = out_root.join(format!("t1_{:05}.csv", strat_id));
                            let _ = fs::rename("trade_list.csv", &out_path);

                            let (n_rows, n_windows, oos_pnl_sum) = summarize_trade_csv(&out_path);

                            writeln!(index,
                                "{:05},{},{},{},{:.3},{},{:.3},{:.1},{},{},{},{},{:.4}",
                                strat_id, sig.name,
                                if use_tp { "tp_on" } else { "tp_off" },
                                use_tp, tp_pct, mh, slip, ps, asset.tag,
                                out_path.display(),
                                n_rows, n_windows, oos_pnl_sum
                            ).unwrap();

                            if strat_id % 100 == 0 {
                                let frac = (strat_id + 1) as f64 / total_count as f64;
                                let eta_s = t_start.elapsed().as_secs_f64() / frac.max(1e-9) * (1.0 - frac);
                                eprintln!("  {:05}/{}  ({:.1}%) ETA {:.0}s | last: {} {} mh={} slip={:.3} ps={:.0} rows={} wfo_w={} oos_pnl={:.2} elapsed={:.2}s",
                                    strat_id, total_count, frac * 100.0, eta_s,
                                    asset.tag, sig.name, mh, slip, ps, n_rows, n_windows, oos_pnl_sum, elapsed);
                            }
                            strat_id += 1;
                        }
                    }
                }
            }
        }
    }

    let total = t_start.elapsed().as_secs_f64();
    eprintln!("DONE — {} strategies in {:.1}s ({:.2}s/strategy)",
        strat_id, total, total / strat_id as f64);
}

fn silence_stdout<R, F: FnOnce() -> R>(f: F) -> R {
    use std::os::fd::AsRawFd;
    let saved = unsafe { libc::dup(1) };
    let devnull = fs::OpenOptions::new()
        .write(true).open("/dev/null").expect("open /dev/null");
    unsafe { libc::dup2(devnull.as_raw_fd(), 1); }
    let r = f();
    unsafe { libc::dup2(saved, 1); libc::close(saved); }
    r
}

fn summarize_trade_csv(path: &PathBuf) -> (usize, usize, f64) {
    let s = match fs::read_to_string(path) { Ok(s) => s, Err(_) => return (0, 0, 0.0) };
    let mut n_rows = 0usize;
    let mut wfo_windows = std::collections::HashSet::<String>::new();
    let mut oos_pnl = 0.0f64;
    for (i, line) in s.lines().enumerate() {
        if i == 0 { continue; }
        n_rows += 1;
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 15 { continue; }
        let window = cols[1].to_string();
        let sample = cols[2];
        if window.starts_with('W') { wfo_windows.insert(window); }
        if sample.contains("OOS") {
            if let Ok(p) = cols[14].parse::<f64>() { oos_pnl += p; }
        }
    }
    (n_rows, wfo_windows.len(), oos_pnl)
}
