//! Cross-language parity harness binary (roadmap item 09, DSR).
//! Static runner used by tools/parity_dsr.py.
//!
//! Reads a fixture file (path = argv[1]) of pipe-delimited cases and emits
//! one `key=value` line per scalar, mirroring the Python side. Each line is
//! `<name>|<sharpe>|<trial_sharpes csv>|<returns csv>`; numbers are written
//! by the Python harness with %.17g so parsing back to f64 is bit-identical.
//! NaN is emitted as the literal `nan` on both sides.

#![cfg(feature = "dsr")]

use quant_research_framework_rs::dsr::{deflated_sharpe_ratio, expected_max_sharpe_under_null, sharpe_per_observation};

fn fmt(v: f64) -> String {
    if v.is_nan() { "nan".to_string() } else { format!("{:.12}", v) }
}

fn parse_csv(field: &str) -> Vec<f64> {
    let field = field.trim();
    if field.is_empty() {
        return Vec::new();
    }
    field.split(',').map(|s| s.trim().parse::<f64>().expect("parse f64")).collect()
}

fn main() {
    let path = std::env::args().nth(1).expect("fixture file path arg");
    let contents = std::fs::read_to_string(&path).expect("read fixture file");
    for line in contents.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let parts: Vec<&str> = line.split('|').collect();
        assert!(parts.len() == 4, "bad fixture line: {line}");
        let name = parts[0].trim();
        let sharpe: f64 = parts[1].trim().parse().expect("parse sharpe");
        let trials = parse_csv(parts[2]);
        let returns = parse_csv(parts[3]);
        let sr0 = expected_max_sharpe_under_null(&trials);
        let dsr = deflated_sharpe_ratio(sharpe, &trials, &returns);
        println!("{name}_sr0={}", fmt(sr0));
        println!("{name}_dsr={}", fmt(dsr));
        if name == "own_observation_counts" {
            let observed = sharpe_per_observation(&returns);
            assert!((observed - sharpe).abs() < 1e-12);
            println!("{name}_sr={}", fmt(observed));
        }
    }
}
