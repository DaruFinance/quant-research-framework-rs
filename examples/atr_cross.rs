//! Example strategy: ATR-cross with an RSI confluence filter.
//!
//! Primary signal: 3-bar SMA of ATR(lb) crosses 50-bar EMA of ATR(lb).
//!   - cross up  → go long
//!   - cross down → go short
//! Confluence filter: RSI(14) on the previous bar ≥ 50, else drop the signal.
//!
//! This mirrors the proprietary `ATR_x_EMA50_RSIge50` spec in the sibling
//! run_strategies.py — but written the way an end-user would: one file,
//! indicators inlined, no framework metadata. The whole strategy is the
//! `atr_cross_rsi` function; the library (src/lib.rs) handles everything
//! else (IS/OOS split, optimiser, walk-forward, robustness, MC, exports).
//!
//! Run with:
//!   cargo run --release --example atr_cross
//!   cargo run --release --example atr_cross -- path/to/ohlc.csv

use quant_research_framework_rs::{run_with_csv, Bar};

// ---------------------------------------------------------------------------
// Indicators (copied from indicators_tradingview.py for parity with Python).
// ---------------------------------------------------------------------------

/// Wilder's ATR: RMA of true-range over `length` bars.
/// Matches `data['...'].ewm(alpha=1/length, min_periods=length).mean()` in pandas.
fn compute_atr(bars: &[Bar], length: usize) -> Vec<f64> {
    let n = bars.len();
    let mut out = vec![f64::NAN; n];
    if n < 2 || length == 0 {
        return out;
    }
    let alpha = 1.0 / length as f64;
    let mut tr = vec![f64::NAN; n];
    for i in 1..n {
        let hl = bars[i].high - bars[i].low;
        let hc = (bars[i].high - bars[i - 1].close).abs();
        let lc = (bars[i].low - bars[i - 1].close).abs();
        tr[i] = hl.max(hc).max(lc);
    }
    // RMA seeded at the first `length` bars (min_periods=length)
    let mut sum = 0.0;
    let mut count = 0usize;
    let mut rma = f64::NAN;
    for i in 1..n {
        let v = tr[i];
        if v.is_nan() {
            continue;
        }
        if count < length {
            sum += v;
            count += 1;
            if count == length {
                rma = sum / length as f64;
                out[i] = rma;
            }
        } else {
            rma = alpha * v + (1.0 - alpha) * rma;
            out[i] = rma;
        }
    }
    out
}

/// Simple moving average. Returns NaN before enough bars are accumulated.
fn compute_sma(series: &[f64], length: usize) -> Vec<f64> {
    let n = series.len();
    let mut out = vec![f64::NAN; n];
    if length == 0 || n < length {
        return out;
    }
    for i in (length - 1)..n {
        let window = &series[i + 1 - length..=i];
        if window.iter().any(|v| v.is_nan()) {
            continue;
        }
        out[i] = window.iter().sum::<f64>() / length as f64;
    }
    out
}

/// Exponential moving average (NaN-aware, seeded at first non-NaN).
/// Equivalent to `series.ewm(span=span, adjust=False).mean()`.
fn ewm(series: &[f64], span: usize) -> Vec<f64> {
    let alpha = 2.0 / (span as f64 + 1.0);
    let n = series.len();
    let mut out = vec![f64::NAN; n];
    let mut state = f64::NAN;
    for i in 0..n {
        let v = series[i];
        if v.is_nan() {
            continue;
        }
        state = if state.is_nan() { v } else { alpha * v + (1.0 - alpha) * state };
        out[i] = state;
    }
    out
}

/// RSI with Wilder smoothing (ewm com=length-1, min_periods=length).
fn compute_rsi(bars: &[Bar], length: usize) -> Vec<f64> {
    let n = bars.len();
    let mut out = vec![f64::NAN; n];
    if n < 2 || length == 0 {
        return out;
    }
    let alpha = 1.0 / length as f64;
    let mut gain_sum = 0.0;
    let mut loss_sum = 0.0;
    let mut count = 0usize;
    let mut avg_gain = f64::NAN;
    let mut avg_loss = f64::NAN;
    for i in 1..n {
        let delta = bars[i].close - bars[i - 1].close;
        let g = if delta > 0.0 { delta } else { 0.0 };
        let l = if delta < 0.0 { -delta } else { 0.0 };
        if count < length {
            gain_sum += g;
            loss_sum += l;
            count += 1;
            if count == length {
                avg_gain = gain_sum / length as f64;
                avg_loss = loss_sum / length as f64;
            }
        } else {
            avg_gain = alpha * g + (1.0 - alpha) * avg_gain;
            avg_loss = alpha * l + (1.0 - alpha) * avg_loss;
        }
        if count >= length {
            out[i] = if avg_loss == 0.0 {
                100.0
            } else {
                let rs = avg_gain / avg_loss;
                100.0 - 100.0 / (1.0 + rs)
            };
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Strategy
// ---------------------------------------------------------------------------

const ATR_PARTNER: usize = 50; // slow ATR-EMA partner length, from the spec
const RSI_LEN: usize = 14;     // standard RSI window for the confluence
const RSI_THRESHOLD: f64 = 50.0;

/// Raw-signals function. Signature is fixed by the backtester:
///   fn(&[Bar], usize) -> Vec<i8>
///
/// Return `raw[i] = +1` for long entry, `-1` for short, `0` for no signal.
/// Indexing rule: `raw[i]` must only depend on information available at
/// bar `i-1` (no look-ahead).
fn atr_cross_rsi(bars: &[Bar], lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < 4 {
        return raw;
    }

    let atr = compute_atr(bars, lb);
    let fast = compute_sma(&atr, 3);
    let slow = ewm(&atr, ATR_PARTNER);
    let rsi = compute_rsi(bars, RSI_LEN);

    for i in 2..n {
        // Use previous bar's values (no look-ahead).
        let f1 = fast[i - 1];
        let s1 = slow[i - 1];
        let f2 = fast[i - 2];
        let s2 = slow[i - 2];
        if f1.is_nan() || s1.is_nan() || f2.is_nan() || s2.is_nan() {
            continue;
        }

        let cross_up = f1 > s1 && f2 <= s2;
        let cross_down = f1 < s1 && f2 >= s2;

        // RSI confluence on the previous bar.
        let rsi_ok = rsi[i - 1] >= RSI_THRESHOLD;
        if !rsi_ok {
            continue;
        }

        if cross_up {
            raw[i] = 1;
        } else if cross_down {
            raw[i] = -1;
        }
    }
    raw
}

fn main() {
    run_with_csv("data/SOLUSDT_1h.csv", "ATR-cross-RSIge50", atr_cross_rsi);
}
