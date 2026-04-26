//! Custom regime-detector contract example.
//!
//! Mirrors the Python `examples/regime_custom/regime_custom.py` demo. The
//! library exposes a `RegimeConfig { labels, detector }` struct and a
//! `run_with_regime()` entry point; user code wires up its own detector
//! (any function with the `RegimeDetectorFn = fn(&[Bar]) -> Vec<u8>`
//! signature) and a label set of length 2..=5.
//!
//! In v0.2.1 the engine actually consumes both of those: the WFO loop
//! pre-computes regimes once via your detector, optimises one LB per
//! label on each IS window, and rotates the active LB bar-by-bar in OOS.
//! The WFO walk cadence is driven by `WFO_TRIGGER_VAL` — regime flips
//! never re-anchor the IS window.
//!
//! Run:
//!   cargo run --release --example regime_custom
//!   cargo run --release --example regime_custom -- path/to/ohlc.csv

use quant_research_framework_rs::{run_with_regime, Bar, RegimeConfig, compute_ema};

// 4-regime trend × volatility detector (CalmUp / CalmDown / VolUp / VolDown).
const VOL4_LABELS: [&str; 4] = ["CalmUp", "CalmDown", "VolUp", "VolDown"];

fn detect_regimes_vol4(bars: &[Bar]) -> Vec<u8> {
    let n = bars.len();
    let mut out = vec![0u8; n];                 // default = CalmUp (label 0)
    if n < 251 { return out; }

    let closes: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let mut rets = vec![0.0f64; n];
    for i in 1..n {
        let denom = closes[i - 1].max(1e-12);
        rets[i] = (closes[i] - closes[i - 1]) / denom;
    }

    // 50-bar rolling stdev (computed from rets[i-50..i], so look-ahead-clean for i).
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

// Reference EMA-crossover strategy. The signal fn is consulted for the
// IS/OOS baseline + classic optimiser phases; the WFO+regime path uses a
// regime-rotated EMA crossover internally.
fn ema_crossover(bars: &[Bar], lb: usize) -> Vec<i8> {
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
    let regime_cfg = RegimeConfig::new(
        VOL4_LABELS.iter().map(|s| s.to_string()).collect(),
        detect_regimes_vol4,
    );

    let csv_path = std::env::args().nth(1)
        .unwrap_or_else(|| "data/SOLUSDT_1h.csv".to_string());
    let bars = quant_research_framework_rs::load_ohlc(&csv_path);
    println!("Loaded {} bars from {}", bars.len(), csv_path);
    run_with_regime(&bars, "Regime-custom-vol4", ema_crossover, regime_cfg);
}
