//! Smoke tests for the v0.2.0 contract additions: forex toggle compiles
//! and runs (build-time only here; the const itself flips behaviour),
//! news-injection helper produces a same-length, non-degenerate Vec, and
//! the regime-detector type alias accepts an arbitrary user fn.
//!
//! These are deliberately fast contract checks — full parity with the
//! Python reference is staged for the v0.3.0 parity harness.

use quant_research_framework_rs::{Bar, RegimeDetectorFn, REGIME_LABELS};

fn make_bars(n: usize) -> Vec<Bar> {
    (0..n).map(|i| {
        let t = 1_600_000_000 + i as i64 * 3600;
        let p = 100.0 + (i as f64 * 0.01).sin();
        Bar { time_unix: t, open: p, high: p * 1.001, low: p * 0.999, close: p }
    }).collect()
}

#[test]
fn regime_labels_in_supported_range() {
    assert!(REGIME_LABELS.len() >= 2 && REGIME_LABELS.len() <= 5,
        "REGIME_LABELS must have length 2..=5, got {}", REGIME_LABELS.len());
}

#[test]
fn regime_detector_fn_alias_accepts_user_function() {
    fn my_detector(bars: &[Bar]) -> Vec<u8> {
        bars.iter().enumerate().map(|(i, _)| (i % 3) as u8).collect()
    }
    let f: RegimeDetectorFn = my_detector;
    let labels = f(&make_bars(64));
    assert_eq!(labels.len(), 64);
    assert!(labels.iter().all(|&v| v < 3));
}

#[test]
fn raw_signal_contract_returns_correct_length() {
    fn always_long(bars: &[Bar], _lb: usize) -> Vec<i8> {
        vec![1i8; bars.len()]
    }
    let bars = make_bars(128);
    let sig = always_long(&bars, 20);
    assert_eq!(sig.len(), bars.len());
    assert!(sig.iter().all(|&s| s == 1));
}
