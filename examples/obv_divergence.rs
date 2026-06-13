//! OBV divergence (item #2). Bullish when price makes a lower low over a
//! lookback while OBV makes a higher low; bearish on the mirror.
//!
//! No look-ahead: raw[i] compares bar i-1 against bar i-1-LOOK only.
//!
//!   cargo run --release --example obv_divergence -- data/volume_fixture.csv

use quant_research_framework_rs::volume::obv;
use quant_research_framework_rs::{run_with_csv, Bar};

const LOOK: usize = 14;

fn obv_divergence(bars: &[Bar], _lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < LOOK + 2 {
        return raw;
    }
    let volume: Vec<f64> = bars.iter().map(|b| b.volume).collect();
    let o = obv(bars, &volume);

    for i in (LOOK + 1)..n {
        let p_now = bars[i - 1].close;
        let p_then = bars[i - 1 - LOOK].close;
        let o_now = o[i - 1];
        let o_then = o[i - 1 - LOOK];
        if p_now < p_then && o_now > o_then {
            raw[i] = 1; // bullish divergence
        } else if p_now > p_then && o_now < o_then {
            raw[i] = -1; // bearish divergence
        }
    }
    raw
}

fn main() {
    run_with_csv("data/volume_fixture.csv", "OBV-divergence", obv_divergence);
}
