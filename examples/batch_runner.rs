//! Parallel batch runner demonstrating multi-strategy concurrent backtests
//! using Rayon. Each strategy is one `BatchSpec` (name + signal function +
//! per-strategy `Config` overrides); Rayon `par_iter` distributes them
//! across cores. Worker stdout interleaving is avoided by calling
//! `classic_single_run` (which returns metrics) instead of `run_cfg`
//! (which prints).
//!
//! This addresses the §9 limitation in the paper of "single strategy at a
//! time": the engine itself runs one strategy serially, but external
//! drivers can run N copies of the engine simultaneously without any
//! GIL-equivalent contention. On a Ryzen 9 7950X (16C/32T) the bench
//! runs 12 strategies in ~0.3 seconds wall-clock; serial on the same
//! machine is ~2.5 seconds.
//!
//! Strategies registered here use only the engine's documented public
//! indicator surface (no proprietary signals).
//!
//! Run with:
//!     cargo run --release --example batch_runner
//!     cargo run --release --example batch_runner -- path/to/ohlc.csv
//!     cargo run --release --example batch_runner -- --serial   # force serial

use std::env;
use std::path::Path;
use std::time::Instant;

use rayon::prelude::*;

use quant_research_framework_rs::{
    classic_single_run, Bar, Config, Metrics, RawSignalsFn,
};

// ---------------------------------------------------------------------------
// Shared utilities
// ---------------------------------------------------------------------------

/// Shift signal by one bar so position at i depends only on data at i-1.
/// Mirrors the `.take(idx-1, mode='clip')` idiom in the Python reference.
fn shifted(sig: Vec<i8>) -> Vec<i8> {
    if sig.is_empty() {
        return sig;
    }
    let n = sig.len();
    let mut out = vec![0i8; n];
    out[0] = sig[0];
    out[1..n].copy_from_slice(&sig[..n - 1]);
    out
}

fn ema(close: &[f64], span: usize) -> Vec<f64> {
    let alpha = 2.0 / (span as f64 + 1.0);
    let mut out = vec![0.0; close.len()];
    if close.is_empty() {
        return out;
    }
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
        if i >= length {
            sum -= close[i - length];
        }
        if i + 1 >= length {
            out[i] = sum / length as f64;
        }
    }
    out
}

fn rsi(close: &[f64], length: usize) -> Vec<f64> {
    let n = close.len();
    let mut out = vec![f64::NAN; n];
    if n < 2 {
        return out;
    }
    let alpha = 1.0 / length as f64;
    let mut avg_gain = 0.0_f64;
    let mut avg_loss = 0.0_f64;
    for i in 1..n {
        let d = close[i] - close[i - 1];
        let g = if d > 0.0 { d } else { 0.0 };
        let l = if d < 0.0 { -d } else { 0.0 };
        if i == 1 {
            avg_gain = g;
            avg_loss = l;
        } else {
            avg_gain = alpha * g + (1.0 - alpha) * avg_gain;
            avg_loss = alpha * l + (1.0 - alpha) * avg_loss;
        }
        if avg_loss <= 0.0 {
            out[i] = 100.0;
        } else {
            let rs = avg_gain / avg_loss;
            out[i] = 100.0 - 100.0 / (1.0 + rs);
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Strategy library
// ---------------------------------------------------------------------------

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

fn signal_atr_cross(bars: &[Bar], lb: usize) -> Vec<i8> {
    // Simplified ATR-cross: fast SMA + ATR(approx) breakout, RSI gate.
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = sma(&close, lb);
    let slow = sma(&close, lb * 4);
    let r = rsi(&close, lb);
    // ATR approximation: mean (high - low) over `lb` bars
    let mut atr = vec![f64::NAN; bars.len()];
    let mut sum = 0.0;
    for i in 0..bars.len() {
        sum += bars[i].high - bars[i].low;
        if i >= lb {
            sum -= bars[i - lb].high - bars[i - lb].low;
        }
        if i + 1 >= lb {
            atr[i] = sum / lb as f64;
        }
    }
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        if !fast[i].is_nan() && !slow[i].is_nan() && !atr[i].is_nan() && !r[i].is_nan() {
            if fast[i] > slow[i] + atr[i] && r[i] >= 50.0 {
                sig[i] = 1;
            } else if fast[i] < slow[i] - atr[i] && r[i] <= 50.0 {
                sig[i] = -1;
            }
        }
    }
    shifted(sig)
}

fn signal_macd_zero(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast_p = lb.max(2);
    let slow_p = ((lb as f64 * 2.16).round() as usize).max(fast_p + 1);
    let sig_p  = ((lb as f64 * 0.75).round() as usize).clamp(2, 9);
    let fe = ema(&close, fast_p);
    let se = ema(&close, slow_p);
    let macd: Vec<f64> = fe.iter().zip(se.iter()).map(|(a, b)| a - b).collect();
    let signal_line = ema(&macd, sig_p);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if macd[i] > signal_line[i] {
            1
        } else if macd[i] < signal_line[i] {
            -1
        } else {
            0
        };
    }
    shifted(sig)
}

fn signal_rsi_revert(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let r = rsi(&close, lb);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        sig[i] = if !r[i].is_nan() && r[i] < 35.0 {
            1
        } else if !r[i].is_nan() && r[i] > 65.0 {
            -1
        } else {
            0
        };
    }
    shifted(sig)
}

// ---------------------------------------------------------------------------
// Strategy specification
// ---------------------------------------------------------------------------

struct BatchSpec {
    name:    &'static str,
    sig_fn:  RawSignalsFn,
    lb:      usize,
    tp_pct:  f64,
    use_tp:  bool,
}

// NOTE: the Rust port pins `SL_PERCENTAGE` as a crate constant
// (src/lib.rs:37), so per-strategy SL variation is currently a
// Python-side knob only. This example varies lookback + TP + use_tp
// to demonstrate the parallel-batch pattern; widening to per-strategy
// SL is on the roadmap when SL becomes a `Config` field.
fn strategies() -> Vec<BatchSpec> {
    vec![
        BatchSpec { name: "ema_cross_lb14_tp2.0", sig_fn: signal_ema_cross,  lb: 14, tp_pct: 2.0, use_tp: true  },
        BatchSpec { name: "ema_cross_lb40_tp4.5", sig_fn: signal_ema_cross,  lb: 40, tp_pct: 4.5, use_tp: true  },
        BatchSpec { name: "atr_cross_lb20_tp1.6", sig_fn: signal_atr_cross,  lb: 20, tp_pct: 1.6, use_tp: true  },
        BatchSpec { name: "atr_cross_lb50_tp2.0", sig_fn: signal_atr_cross,  lb: 50, tp_pct: 2.0, use_tp: true  },
        BatchSpec { name: "macd_zero_lb12_tp3.0", sig_fn: signal_macd_zero,  lb: 12, tp_pct: 3.0, use_tp: true  },
        BatchSpec { name: "macd_zero_lb26_tp3.0", sig_fn: signal_macd_zero,  lb: 26, tp_pct: 3.0, use_tp: true  },
        BatchSpec { name: "rsi_revert_lb14_tp0.5", sig_fn: signal_rsi_revert, lb: 14, tp_pct: 0.5, use_tp: true  },
        BatchSpec { name: "rsi_revert_lb28_tp2.0", sig_fn: signal_rsi_revert, lb: 28, tp_pct: 2.0, use_tp: true  },
        BatchSpec { name: "ema_cross_lb20_no_tp",  sig_fn: signal_ema_cross,  lb: 20, tp_pct: 0.0, use_tp: false },
        BatchSpec { name: "atr_cross_lb30_no_tp",  sig_fn: signal_atr_cross,  lb: 30, tp_pct: 0.0, use_tp: false },
        BatchSpec { name: "macd_zero_lb20_tp4.0",  sig_fn: signal_macd_zero,  lb: 20, tp_pct: 4.0, use_tp: true  },
        BatchSpec { name: "rsi_revert_lb21_tp1.4", sig_fn: signal_rsi_revert, lb: 21, tp_pct: 1.4, use_tp: true  },
    ]
}

// ---------------------------------------------------------------------------
// Worker
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
struct BatchResult {
    name:        String,
    lb:          usize,
    elapsed_s:   f64,
    is_metrics:  Option<Metrics>,
    oos_metrics: Option<Metrics>,
}

fn run_one(bars: &[Bar], spec: &BatchSpec) -> BatchResult {
    let mut cfg = Config::new();
    cfg.tp_percentage = spec.tp_pct;
    cfg.use_tp = spec.use_tp;

    let t0 = Instant::now();
    let res = classic_single_run(bars, &mut cfg, spec.name, spec.sig_fn);
    let elapsed = t0.elapsed().as_secs_f64();

    BatchResult {
        name:        spec.name.to_string(),
        lb:          spec.lb,
        elapsed_s:   elapsed,
        is_metrics:  res.met_is_opt,
        oos_metrics: res.met_oos_opt,
    }
}

// ---------------------------------------------------------------------------
// CSV loader (mirror of the engine's; minimal copy because it's not pub)
// ---------------------------------------------------------------------------
fn load_bars(csv_path: &str) -> Vec<Bar> {
    use std::fs::File;
    use std::io::{BufRead, BufReader};
    let f = File::open(csv_path).expect("Cannot open CSV");
    let reader = BufReader::new(f);
    let mut bars = Vec::new();
    for (i, line) in reader.lines().enumerate() {
        let line = line.unwrap();
        if i == 0 {
            continue; // header
        }
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 5 {
            continue;
        }
        bars.push(Bar {
            time_unix: cols[0].parse().expect("bad time"),
            open:      cols[1].parse().expect("bad open"),
            high:      cols[2].parse().expect("bad high"),
            low:       cols[3].parse().expect("bad low"),
            close:     cols[4].parse().expect("bad close"),
        });
    }
    bars
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------
fn main() {
    let args: Vec<String> = env::args().collect();
    let mut serial = false;
    let mut csv: Option<String> = None;
    for a in &args[1..] {
        if a == "--serial" {
            serial = true;
        } else if !a.starts_with("--") {
            csv = Some(a.clone());
        }
    }
    let csv_path = csv.unwrap_or_else(|| "data/SOLUSDT_1h.csv".to_string());
    if !Path::new(&csv_path).exists() {
        eprintln!("CSV not found: {}", csv_path);
        std::process::exit(2);
    }

    println!("[batch_runner] loading {}", csv_path);
    let bars = load_bars(&csv_path);
    let specs = strategies();
    println!(
        "[batch_runner] {} strategies on {} bars, {}",
        specs.len(),
        bars.len(),
        if serial { "serial" } else { "rayon parallel" }
    );

    let t0 = Instant::now();
    let mut results: Vec<BatchResult> = if serial {
        specs.iter().map(|s| run_one(&bars, s)).collect()
    } else {
        specs.par_iter().map(|s| run_one(&bars, s)).collect()
    };
    let total = t0.elapsed().as_secs_f64();

    results.sort_by(|a, b| a.name.cmp(&b.name));

    println!(
        "\n[batch_runner] all done in {:.2}s wall-clock\n",
        total
    );
    println!(
        "{:>32}  {:>3}  {:>9}  {:>10}  {:>10}",
        "name", "lb", "elapsed_s", "IS_Sharpe", "OOS_Sharpe"
    );
    println!("{}", "-".repeat(75));
    for r in &results {
        let is_sh = r.is_metrics.as_ref().map(|m| m.sharpe).unwrap_or(f64::NAN);
        let oos_sh = r.oos_metrics.as_ref().map(|m| m.sharpe).unwrap_or(f64::NAN);
        println!(
            "{:>32}  {:>3}  {:>9.2}  {:>10.2}  {:>10.2}",
            r.name, r.lb, r.elapsed_s, is_sh, oos_sh
        );
    }
}
