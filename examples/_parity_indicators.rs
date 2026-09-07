//! Cross-language parity harness binary (roadmap item 5, indicators).
//! Static runner used by tools/parity_indicators.py.
//!
//! Reads a fixture file (path = argv[1]): a single data line
//! `<open csv>|<high csv>|<low csv>|<close csv>`, numbers written by the
//! Python harness with %.17g so parsing back to f64 is bit-identical. Emits
//! one `key=value` line per (indicator, length, bar_index) point, mirroring
//! the Python side. NaN is emitted as the literal `nan` and +/-inf as
//! `inf`/`-inf` on both sides.

#![cfg(feature = "indicators")]

use quant_research_framework_rs::indicators::{
    compute_atr, compute_ema, compute_macd, compute_rsi, compute_sma, compute_stoch, ema,
};

fn fmt(v: f64) -> String {
    if v.is_nan() {
        "nan".to_string()
    } else if v.is_infinite() {
        if v > 0.0 { "inf".to_string() } else { "-inf".to_string() }
    } else {
        format!("{:.12}", v)
    }
}

fn parse_csv(field: &str) -> Vec<f64> {
    let field = field.trim();
    if field.is_empty() {
        return Vec::new();
    }
    field
        .split(',')
        .map(|s| {
            let s = s.trim();
            match s {
                "nan" => f64::NAN,
                "inf" => f64::INFINITY,
                "-inf" => f64::NEG_INFINITY,
                _ => s.parse::<f64>().expect("parse f64"),
            }
        })
        .collect()
}

fn emit(prefix: &str, vec: &[f64]) {
    for (i, v) in vec.iter().enumerate() {
        println!("{prefix}#{i}={}", fmt(*v));
    }
}

const SMA_LENGTHS: &[usize] = &[3, 14];
const EMA_LENGTHS: &[usize] = &[14, 50];
const RSI_LENGTHS: &[usize] = &[14, 7];
const ATR_LENGTHS: &[usize] = &[14, 20];
const STOCH_LENGTHS: &[usize] = &[14, 21];
const MACD_PARAMS: &[(usize, usize, usize)] = &[(12, 26, 9), (5, 13, 4)];
const IOI_ATR_LENGTH: usize = 14;
const IOI_EMA_SPAN: usize = 50;
const IOI_SMA_LENGTH: usize = 3;

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture file");
    let mut data_line = "";
    for line in contents.lines() {
        let t = line.trim();
        if t.is_empty() || t.starts_with('#') {
            continue;
        }
        data_line = t;
        break;
    }
    let parts: Vec<&str> = data_line.split('|').collect();
    assert!(parts.len() == 4, "bad fixture line");
    let _open = parse_csv(parts[0]);
    let high = parse_csv(parts[1]);
    let low = parse_csv(parts[2]);
    let close = parse_csv(parts[3]);

    for &l in SMA_LENGTHS {
        emit(&format!("sma_{l}"), &compute_sma(&close, l));
    }
    for &l in EMA_LENGTHS {
        emit(&format!("ema_{l}"), &compute_ema(&close, l));
    }
    for &(f, s, sig) in MACD_PARAMS {
        let (macd, signal) = compute_macd(&close, f, s, sig);
        emit(&format!("macd_{f}_{s}_{sig}"), &macd);
        emit(&format!("macdsig_{f}_{s}_{sig}"), &signal);
    }
    for &l in RSI_LENGTHS {
        emit(&format!("rsi_{l}"), &compute_rsi(&close, l));
    }
    for &l in ATR_LENGTHS {
        emit(&format!("atr_{l}"), &compute_atr(&high, &low, &close, l));
    }
    for &l in STOCH_LENGTHS {
        emit(&format!("stoch_{l}"), &compute_stoch(&high, &low, &close, l));
    }

    // Indicator-of-indicator: ema/sma OF an ATR (leading-NaN warmup input).
    // Uses the NaN-aware `ema` (NOT compute_ema) so the leading-NaN ATR head
    // is skipped, matching pandas `atr.ewm(span,adjust=False).mean()`.
    let atr = compute_atr(&high, &low, &close, IOI_ATR_LENGTH);
    emit(
        &format!("ioi_ema_atr{IOI_ATR_LENGTH}_{IOI_EMA_SPAN}"),
        &ema(&atr, IOI_EMA_SPAN),
    );
    emit(
        &format!("ioi_sma_atr{IOI_ATR_LENGTH}_{IOI_SMA_LENGTH}"),
        &compute_sma(&atr, IOI_SMA_LENGTH),
    );
}
