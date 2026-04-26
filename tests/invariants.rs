//! Property checks on actual engine outputs. Where `behavioural.rs`
//! verifies "does the flag change anything", these tests verify "does the
//! engine respect the invariants it claims".

use quant_research_framework_rs::{
    Bar, Config, RegimeConfig, default_regime_detector, parse_signals_with_flags,
    compute_in_flags, compute_ema, parse_signals,
};

fn make_bars(n: usize, start_unix: i64, interval_s: i64, seed: u64) -> Vec<Bar> {
    use rand::{SeedableRng, Rng, rngs::StdRng};
    let mut rng = StdRng::seed_from_u64(seed);
    let mut p = 100.0f64;
    (0..n).map(|i| {
        let t = start_unix + i as i64 * interval_s;
        let r: f64 = rng.gen::<f64>() * 0.02 - 0.01;
        p *= (1.0 + r).max(0.5);
        Bar { time_unix: t, open: p, high: p * 1.002, low: p * 0.998, close: p }
    }).collect()
}

fn ny_hour(unix_ts: i64) -> u32 {
    use chrono::{TimeZone, Utc, Timelike};
    use chrono_tz::America::New_York;
    Utc.timestamp_opt(unix_ts, 0).single().unwrap().with_timezone(&New_York).hour()
}

// ----------------------------------------------------------------------------
// Session: parse_signals with NY-tz flags must drop signals on bars whose
// NY hour falls outside [start_hour, end_hour). Verify the produced signal
// vector has no flip codes (1, 3) at any out-of-session bar.
// ----------------------------------------------------------------------------
#[test]
fn parse_signals_emits_no_flips_on_out_of_session_bars() {
    let bars = make_bars(2_000, 1_600_000_000, 3600, 7);
    let cfg = Config::new().with_sessions(true, 8, 17);
    let in_flags = compute_in_flags(&bars, &cfg);

    // Build a dense alternating raw to maximise the chance of flips on
    // every bar — if the session masking is broken we'll see flip codes
    // pop up on out-of-session bars.
    let raw: Vec<i8> = (0..bars.len()).map(|i| if i % 2 == 0 { 1 } else { -1 }).collect();
    let sig = parse_signals_with_flags(&raw, Some(&in_flags));

    let mut violations = Vec::new();
    for i in 0..bars.len() {
        if !in_flags[i] && (sig[i] == 1 || sig[i] == 3) {
            violations.push((i, sig[i], ny_hour(bars[i].time_unix)));
        }
    }
    assert!(violations.is_empty(),
        "Session invariant broken — {} out-of-session bars carry flip codes. \
         First few: {:?}", violations.len(), &violations[..violations.len().min(5)]);
}

// ----------------------------------------------------------------------------
// `compute_in_flags` must align bar-by-bar with what `backtest_core`
// considers in-session. Build a known set of bars and verify the helper.
// ----------------------------------------------------------------------------
#[test]
fn compute_in_flags_uses_ny_local_hours() {
    let bars = make_bars(48, 1_600_000_000, 3600, 11);
    let cfg = Config::new().with_sessions(true, 8, 17);
    let flags = compute_in_flags(&bars, &cfg);
    assert_eq!(flags.len(), bars.len());
    for (i, b) in bars.iter().enumerate() {
        let h = ny_hour(b.time_unix);
        let want = h >= 8 && h < 17;
        assert_eq!(flags[i], want,
            "bar {} (NY hour {}): in_flags expected {}, got {}", i, h, want, flags[i]);
    }
}

#[test]
fn compute_in_flags_returns_all_true_when_sessions_off() {
    let bars = make_bars(48, 1_600_000_000, 3600, 13);
    let cfg = Config::new();   // use_sessions defaults to false
    let flags = compute_in_flags(&bars, &cfg);
    assert!(flags.iter().all(|&v| v),
        "with sessions off, every bar should be 'in session'");
}

// ----------------------------------------------------------------------------
// Forex: when use_forex flips on, Config switches to position_size_fx=1.0
// internally and the SL/TP use additive pip-distance arithmetic. The
// engine doesn't expose backtest_core directly, but we can at least assert
// that flipping the flag and round-tripping it through Config preserves
// the new pip semantics.
// ----------------------------------------------------------------------------
#[test]
fn forex_config_switches_to_pip_semantics() {
    let cfg_off = Config::new();
    let cfg_on  = cfg_off.clone().with_forex(true);
    assert!(!cfg_off.use_forex);
    assert!(cfg_on.use_forex);
    // Default pip_size is the 4-decimal-pair value.
    assert!((cfg_on.pip_size - 0.0001).abs() < 1e-12);
    // JPY override must round-trip cleanly.
    let mut jpy = cfg_on.clone();
    jpy.pip_size = 0.01;
    assert!((jpy.pip_size - 0.01).abs() < 1e-12);
}

// ----------------------------------------------------------------------------
// Regime detector: default labels are 0/1/2 (Uptrend/Downtrend/Ranging),
// no value out of range, length matches bars. Plus the actual labelling
// must respect look-ahead — bar i must only consume close/EMA200 for
// indices < i.
// ----------------------------------------------------------------------------
#[test]
fn default_regime_detector_emits_only_valid_labels() {
    let bars = make_bars(2_000, 1_600_000_000, 3600, 17);
    let labels = default_regime_detector(&bars);
    assert_eq!(labels.len(), bars.len());
    for (i, &v) in labels.iter().enumerate() {
        assert!(v < 3, "bar {}: regime label {} out of [0, 2]", i, v);
    }
}

#[test]
fn default_regime_detector_no_lookahead() {
    let bars = make_bars(800, 1_600_000_000, 3600, 19);
    let full = default_regime_detector(&bars);

    // Replace bars[cut..] with garbage and re-run; labels at indices
    // [0..cut] must be unchanged.
    let cut = 400;
    let mut polluted = bars.clone();
    for i in cut..polluted.len() {
        polluted[i].close = f64::NAN;
        polluted[i].open  = f64::NAN;
        polluted[i].high  = f64::NAN;
        polluted[i].low   = f64::NAN;
    }
    let clean = default_regime_detector(&polluted);

    for i in 0..cut {
        assert_eq!(full[i], clean[i],
            "default detector leaks future data — bar {} differs (full={}, clean={})",
            i, full[i], clean[i]);
    }
}

// ----------------------------------------------------------------------------
// REGIME_LABELS / RegimeConfig contract: 2..=5, validated at construction.
// ----------------------------------------------------------------------------
#[test]
fn regime_config_round_trips_each_supported_label_count() {
    fn det(bars: &[Bar]) -> Vec<u8> { vec![0u8; bars.len()] }
    for n in 2..=5 {
        let labels: Vec<String> = (0..n).map(|i| format!("R{i}")).collect();
        let cfg = RegimeConfig::new(labels.clone(), det);
        assert_eq!(cfg.labels, labels);
    }
}

// ----------------------------------------------------------------------------
// parse_signals (the no-flag path) is order-preserving and length-preserving.
// ----------------------------------------------------------------------------
#[test]
fn parse_signals_preserves_length_and_emits_only_valid_codes() {
    let raw: Vec<i8> = vec![1, 1, -1, 0, 1, -1, -1, 0, 1, 1];
    let sig = parse_signals(&raw);
    assert_eq!(sig.len(), raw.len());
    for &c in &sig {
        assert!(matches!(c, 0 | 1 | 2 | 3 | 4),
            "parse_signals emitted invalid code {}", c);
    }
}

#[test]
fn parse_signals_no_lookahead() {
    // Build a long raw signal vector. Compute parse on the full vector,
    // then truncate the back half to noise and re-parse. The first half's
    // sig must be identical.
    use rand::{SeedableRng, Rng, rngs::StdRng};
    let mut rng = StdRng::seed_from_u64(23);
    let n = 500;
    let raw: Vec<i8> = (0..n).map(|_| {
        let v = rng.gen_range(0..3);
        if v == 0 { 1 } else if v == 1 { -1 } else { 0 }
    }).collect();
    let sig_full = parse_signals(&raw);

    let mut polluted = raw.clone();
    for i in 250..polluted.len() { polluted[i] = 0; }
    let sig_clean = parse_signals(&polluted);

    for i in 0..250 {
        assert_eq!(sig_full[i], sig_clean[i],
            "parse_signals leaks future data into bar {}", i);
    }
}

// ----------------------------------------------------------------------------
// EMA: matches the recursive form alpha = 2 / (span+1), warmed at the
// first finite input. Length preserved.
// ----------------------------------------------------------------------------
#[test]
fn compute_ema_matches_recursive_form() {
    let close: Vec<f64> = (1..=200).map(|x| x as f64).collect();
    let span = 20;
    let alpha = 2.0 / (span as f64 + 1.0);
    let ema = compute_ema(&close, span);
    assert_eq!(ema.len(), close.len());
    assert_eq!(ema[0], close[0]);
    for i in 1..close.len() {
        let want = alpha * close[i] + (1.0 - alpha) * ema[i - 1];
        assert!((ema[i] - want).abs() < 1e-9,
            "EMA off at bar {}: got {}, want {}", i, ema[i], want);
    }
}
