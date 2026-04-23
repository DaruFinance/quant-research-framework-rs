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

/// Pandas-style adjusted EWM with `min_periods`. Matches
/// `series.ewm(alpha=alpha, min_periods=mp).mean()` (default `adjust=True`,
/// `ignore_na=False` — NaN entries decay the weight but don't contribute a
/// value). Used as the building block for both compute_atr and compute_rsi
/// so the indicators here track their pandas counterparts in
/// indicators_tradingview.py.
fn ewm_adjusted(series: &[f64], alpha: f64, min_periods: usize) -> Vec<f64> {
    let n = series.len();
    let mut out = vec![f64::NAN; n];
    let gamma = 1.0 - alpha;
    let mut num = 0.0f64;
    let mut den = 0.0f64;
    let mut seen = 0usize;
    for i in 0..n {
        let x = series[i];
        num *= gamma;
        den *= gamma;
        if !x.is_nan() {
            num += x;
            den += 1.0;
            seen += 1;
        }
        if seen >= min_periods && den > 0.0 {
            out[i] = num / den;
        }
    }
    out
}

/// ATR = adjusted EWM (alpha=1/length, min_periods=length) of true range.
/// Matches `compute_atr` in indicators_tradingview.py bit-for-bit.
fn compute_atr(bars: &[Bar], length: usize) -> Vec<f64> {
    let n = bars.len();
    let mut tr = vec![f64::NAN; n];
    if n == 0 {
        return tr;
    }
    // Pandas sets TR[0] = high[0] - low[0] because hc/lc are NaN there and
    // DataFrame.max(axis=1) skips NaN.
    tr[0] = bars[0].high - bars[0].low;
    for i in 1..n {
        let hl = bars[i].high - bars[i].low;
        let hc = (bars[i].high - bars[i - 1].close).abs();
        let lc = (bars[i].low - bars[i - 1].close).abs();
        tr[i] = hl.max(hc).max(lc);
    }
    ewm_adjusted(&tr, 1.0 / length as f64, length)
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

/// RSI = 100 - 100 / (1 + avg_gain/avg_loss) where the averages are pandas
/// adjusted EWM with `com=length-1` (equivalently `alpha = 1/length`).
/// Matches `compute_rsi` in indicators_tradingview.py.
fn compute_rsi(bars: &[Bar], length: usize) -> Vec<f64> {
    let n = bars.len();
    let mut out = vec![f64::NAN; n];
    if n < 2 || length == 0 {
        return out;
    }
    // delta[0] is NaN in pandas (diff), and `delta.where(delta>0, 0)` maps
    // NaN → 0 (since NaN > 0 is False), so gain/loss are 0 at index 0.
    let mut gain = vec![0.0f64; n];
    let mut loss = vec![0.0f64; n];
    for i in 1..n {
        let d = bars[i].close - bars[i - 1].close;
        if d > 0.0 {
            gain[i] = d;
        } else if d < 0.0 {
            loss[i] = -d;
        }
    }
    let alpha = 1.0 / length as f64;
    let avg_gain = ewm_adjusted(&gain, alpha, length);
    let avg_loss = ewm_adjusted(&loss, alpha, length);
    for i in 0..n {
        if avg_gain[i].is_nan() || avg_loss[i].is_nan() {
            continue;
        }
        out[i] = if avg_loss[i] == 0.0 {
            100.0
        } else {
            let rs = avg_gain[i] / avg_loss[i];
            100.0 - 100.0 / (1.0 + rs)
        };
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
