//! ML signal example (pre-computed predictions).
//!
//! Pattern: train your model offline, attach its score for each bar to a
//! sidecar slice (here: a deterministic stand-in derived from price), and
//! threshold it inside the strategy function. The library never sees your
//! model — it only consumes the resulting `Vec<i8>` of long/short intents.
//!
//! This is the recommended path for any model you can train ahead of time
//! (sklearn, lightgbm, torch via ONNX export, even an R model exported as
//! a CSV column). It is the fastest path because there is no per-bar
//! inference inside the inner loop.
//!
//! Look-ahead discipline: the score for bar `i` must only use information
//! available at bar `i-1` or earlier — exactly the same rule as for any
//! other strategy. The example below uses `.windows(...)` on the closes up
//! to (but not including) bar `i` to keep that explicit.
//!
//! Run:
//!   cargo run --release --example ml_precomputed
//!   cargo run --release --example ml_precomputed -- path/to/ohlc.csv

use quant_research_framework_rs::{run_with_csv, Bar};

const LONG_THRESH: f64 = 0.55;
const SHORT_THRESH: f64 = 0.45;

/// Stand-in "model score" — replace with your loaded predictions. Inputs
/// must already respect look-ahead (the helper only reads bars `0..i`).
fn precomputed_scores(bars: &[Bar]) -> Vec<f64> {
    let n = bars.len();
    let mut scores = vec![0.5f64; n];
    if n < 51 { return scores; }
    for i in 51..n {
        let prev = &bars[i - 50 .. i];
        let mean: f64 = prev.iter().map(|b| b.close).sum::<f64>() / 50.0;
        let var: f64 = prev.iter().map(|b| (b.close - mean).powi(2)).sum::<f64>() / 50.0;
        let std = var.sqrt();
        if std == 0.0 || !std.is_finite() { continue; }
        let z = (bars[i - 1].close - mean) / std;
        scores[i] = 1.0 / (1.0 + (-z * 2.0_f64).exp());     // logistic
    }
    scores
}

fn ml_precomputed_signals(bars: &[Bar], _lb: usize) -> Vec<i8> {
    // In production you would load these from disk / a sidecar struct;
    // recomputing here keeps the example self-contained.
    let scores = precomputed_scores(bars);
    let mut raw = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        let s = scores[i];
        if s >= LONG_THRESH       { raw[i] =  1; }
        else if s <= SHORT_THRESH { raw[i] = -1; }
    }
    raw
}

fn main() {
    run_with_csv("data/SOLUSDT_1h.csv", "ML-precomputed", ml_precomputed_signals);
}
