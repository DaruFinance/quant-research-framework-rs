//! Custom regime-detector contract example.
//!
//! Mirrors the Python `examples/regime_custom/regime_custom.py` demo. The
//! library exposes a type alias `RegimeDetectorFn = fn(&[Bar]) -> Vec<u8>`
//! and a `REGIME_LABELS` const slice that names each regime; user code
//! wires up its own detector by passing a function with that signature.
//!
//! Note: the full per-regime LB optimiser, OOS LB rotation and
//! regime-aware filters are scheduled for v0.3.0 in the Rust port. This
//! example demonstrates the *contract* (and how a user's detector would be
//! shaped) so downstream code can already adopt it. Until v0.3.0 lands the
//! detector output is computed and printed but not applied to the
//! backtest's signal pipeline (matching the v0.2.0 stub described in
//! lib.rs).
//!
//! Run:
//!   cargo run --release --example regime_custom
//!   cargo run --release --example regime_custom -- path/to/ohlc.csv

use quant_research_framework_rs::{run_with_csv, Bar, RegimeDetectorFn};

// 4-regime trend × volatility detector (vol-up / vol-down / calm-up / calm-down).
const VOL4_LABELS: &[&str] = &["CalmUp", "CalmDown", "VolUp", "VolDown"];

fn detect_regimes_vol4(bars: &[Bar]) -> Vec<u8> {
    let n = bars.len();
    let mut out = vec![0u8; n];                 // default = CalmUp
    if n < 251 { return out; }

    let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let mut rets = vec![0.0f64; n];
    for i in 1..n { rets[i] = (closes[i] - closes[i - 1]) / closes[i - 1].max(1e-12); }

    // 50-bar rolling stdev, shifted by 1 to keep look-ahead-clean.
    let mut sd = vec![f64::NAN; n];
    for i in 51..n {
        let win = &rets[i - 50 .. i];
        let mean: f64 = win.iter().sum::<f64>() / win.len() as f64;
        let var: f64 = win.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / win.len() as f64;
        sd[i] = var.sqrt();
    }
    // 250-bar rolling median of sd as the vol cutoff.
    let mut cutoff = vec![f64::NAN; n];
    for i in 250..n {
        let mut buf: Vec<f64> = sd[i - 250 .. i].iter().copied().filter(|v| v.is_finite()).collect();
        if buf.is_empty() { continue; }
        buf.sort_by(|a, b| a.partial_cmp(b).unwrap());
        cutoff[i] = buf[buf.len() / 2];
    }

    for i in 51..n {
        if !sd[i].is_finite() || !cutoff[i].is_finite() { continue; }
        let is_vol = sd[i] > cutoff[i];
        let trend = if i >= 51 { closes[i - 1] - closes[i - 51] } else { 0.0 };
        let is_up = trend > 0.0;
        out[i] = match (is_vol, is_up) {
            (false, true)  => 0,    // CalmUp
            (false, false) => 1,    // CalmDown
            (true,  true)  => 2,    // VolUp
            (true,  false) => 3,    // VolDown
        };
    }
    out
}

// Same shape as the reference EMA-crossover strategy — kept here so the
// example can be run end-to-end. When v0.3.0 lands, swap this for a
// regime-aware variant that branches on the detector's output.
fn ema_crossover(bars: &[Bar], lb: usize) -> Vec<i8> {
    use quant_research_framework_rs::compute_ema;
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
    let detector: RegimeDetectorFn = detect_regimes_vol4;
    let labels = VOL4_LABELS;
    println!("Custom detector configured: {:?} (labels = {:?})", detector as usize, labels);
    run_with_csv("data/SOLUSDT_1h.csv", "Regime-custom-vol4", ema_crossover);
}
