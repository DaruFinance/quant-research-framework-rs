//! Volume-confirmed EMA cross. Take the EMA(fast)/EMA(slow) cross
//! only when current volume exceeds k * volume_sma(N).
//!
//! No look-ahead: raw[i] uses EMAs and the volume confirmation evaluated at
//! bar i-1 only.
//!
//!   cargo run --release --example vol_confirmed_ema_cross -- data/volume_fixture.csv

use quant_research_framework_rs::volume::volume_sma;
use quant_research_framework_rs::{compute_ema, run_with_csv, Bar};

const FAST: usize = 12;
const SLOW: usize = 26;
const VOL_LEN: usize = 20;
const K: f64 = 1.5;

fn vol_confirmed_ema_cross(bars: &[Bar], _lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < 3 {
        return raw;
    }
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let volume: Vec<f64> = bars.iter().map(|b| b.volume).collect();
    let fast = compute_ema(&close, FAST);
    let slow = compute_ema(&close, SLOW);
    let vsma = volume_sma(&volume, VOL_LEN);

    for i in 2..n {
        let vconf = !vsma[i - 1].is_nan() && volume[i - 1] > K * vsma[i - 1];
        if !vconf {
            continue;
        }
        let cross_up = fast[i - 1] > slow[i - 1] && fast[i - 2] <= slow[i - 2];
        let cross_down = fast[i - 1] < slow[i - 1] && fast[i - 2] >= slow[i - 2];
        if cross_up {
            raw[i] = 1;
        } else if cross_down {
            raw[i] = -1;
        }
    }
    raw
}

fn main() {
    run_with_csv("data/volume_fixture.csv", "vol-confirmed-EMA-cross", vol_confirmed_ema_cross);
}
