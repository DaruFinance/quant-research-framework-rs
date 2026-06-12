//! Relative-volume breakout (item #2). When relative volume (vol / volSMA)
//! exceeds a threshold AND the prior close breaks the prior-N high/low
//! channel, trade in the breakout direction.
//!
//! No look-ahead: raw[i] uses relvol[i-1] and the Donchian channel formed
//! over bars ending at i-2 (channel EXCLUDES the breakout bar i-1).
//!
//!   cargo run --release --example relvol_breakout -- data/volume_fixture.csv

use quant_research_framework_rs::volume::relative_volume;
use quant_research_framework_rs::{run_with_csv, Bar};

const VOL_LEN: usize = 20;
const RELVOL_K: f64 = 2.0;
const CHANNEL: usize = 20;

fn relvol_breakout(bars: &[Bar], _lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < CHANNEL + 3 {
        return raw;
    }
    let volume: Vec<f64> = bars.iter().map(|b| b.volume).collect();
    let relvol = relative_volume(&volume, VOL_LEN);

    for i in (CHANNEL + 2)..n {
        let rv = relvol[i - 1];
        if rv.is_nan() || rv < RELVOL_K {
            continue;
        }
        // Donchian over the CHANNEL bars ending at i-2 (exclude breakout bar i-1).
        let lo = i - 1 - CHANNEL;
        let hi_end = i - 1; // exclusive -> bars[lo..i-1]
        let mut hh = f64::NEG_INFINITY;
        let mut ll = f64::INFINITY;
        for k in lo..hi_end {
            if bars[k].high > hh {
                hh = bars[k].high;
            }
            if bars[k].low < ll {
                ll = bars[k].low;
            }
        }
        let c1 = bars[i - 1].close;
        if c1 > hh {
            raw[i] = 1;
        } else if c1 < ll {
            raw[i] = -1;
        }
    }
    raw
}

fn main() {
    run_with_csv("data/volume_fixture.csv", "relvol-breakout", relvol_breakout);
}
