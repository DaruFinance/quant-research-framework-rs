//! ML signal example (per-bar callback).
//!
//! Pattern: keep a model in memory and call `predict(features)` once per
//! bar. Slower than `ml_precomputed` because inference happens inside the
//! inner loop, but the only path that supports online / stateful models or
//! mixing training and inference in the same process.
//!
//! The Predictor below is a hand-coded linear model so this example has no
//! extra dependencies. Swap it for a `linfa`/`smartcore` estimator, an
//! `ort`-loaded ONNX session, a `tch` Torch model, or even a Python FFI
//! call — the strategy function only needs `predict(&[f64]) -> f64`.
//!
//! Look-ahead discipline: features for bar `i` must be derived from
//! `bars[..i]` only. The helper `extract_window_features` enforces this.
//!
//! Run:
//!   cargo run --release --example ml_callback
//!   cargo run --release --example ml_callback -- path/to/ohlc.csv

use quant_research_framework_rs::{run_with_csv, Bar};

trait Predictor {
    fn predict(&self, features: &[f64]) -> f64;
}

/// Tiny linear model: weighted sum of normalised lagged returns through a
/// logistic. Stand-in for a real estimator.
struct TinyMomentumModel { weights: [f64; 3] }
impl TinyMomentumModel {
    fn new() -> Self { Self { weights: [0.6, 0.3, 0.1] } }
}
impl Predictor for TinyMomentumModel {
    fn predict(&self, features: &[f64]) -> f64 {
        let n = features.len().min(self.weights.len());
        let z: f64 = (0..n).map(|i| features[i] * self.weights[i]).sum();
        1.0 / (1.0 + (-z).exp())
    }
}

const LONG_THRESH: f64 = 0.55;
const SHORT_THRESH: f64 = 0.45;

fn extract_window_features(bars: &[Bar], i: usize, lb: usize) -> Vec<f64> {
    if i <= lb { return Vec::new(); }
    let closes: Vec<f64> = bars[i - lb .. i].iter().map(|b| b.close).collect();
    if closes.len() < 4 { return Vec::new(); }
    let mut rets = Vec::with_capacity(closes.len() - 1);
    for k in 1..closes.len() {
        if closes[k - 1] <= 0.0 { continue; }
        rets.push((closes[k] / closes[k - 1]).ln());
    }
    if rets.len() < 3 { return Vec::new(); }
    let mean: f64 = rets.iter().sum::<f64>() / rets.len() as f64;
    let var: f64 = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / rets.len() as f64;
    let sd = var.sqrt();
    if sd == 0.0 || !sd.is_finite() { return Vec::new(); }
    let last3 = &rets[rets.len() - 3 ..];
    last3.iter().map(|r| r / sd).collect()
}

// One global model so the strategy fn (which is `fn`, not `Fn`) can use it.
// In a real project you would load weights once from disk in `main` and
// pass a closure / wrap the library entry point with state.
fn ml_callback_signals(bars: &[Bar], lb: usize) -> Vec<i8> {
    let model = TinyMomentumModel::new();
    let mut raw = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        let feats = extract_window_features(bars, i, lb);
        if feats.is_empty() { continue; }
        let score = model.predict(&feats);
        if score >= LONG_THRESH       { raw[i] =  1; }
        else if score <= SHORT_THRESH { raw[i] = -1; }
    }
    raw
}

fn main() {
    run_with_csv("data/SOLUSDT_1h.csv", "ML-callback", ml_callback_signals);
}
