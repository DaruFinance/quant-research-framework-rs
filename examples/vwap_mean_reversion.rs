//! VWAP mean-reversion (item #2). Fade deviations from session-anchored VWAP.
//!
//! No look-ahead: raw[i] uses close[i-1] and vwap[i-1] only. Session resets
//! come from the NY-tz wall clock (ny_session_resets), the same boundary the
//! engine uses for sessions.
//!
//!   cargo run --release --example vwap_mean_reversion -- data/volume_fixture.csv

use quant_research_framework_rs::volume::{ny_session_resets, vwap_session};
use quant_research_framework_rs::{run_with_csv, Bar};

const ANCHOR_HOUR: u32 = 0; // NY midnight session anchor
const BAND: f64 = 0.01; // 1% of VWAP

fn vwap_mean_reversion(bars: &[Bar], _lb: usize) -> Vec<i8> {
    let n = bars.len();
    let mut raw = vec![0i8; n];
    if n < 2 {
        return raw;
    }
    let volume: Vec<f64> = bars.iter().map(|b| b.volume).collect();
    let times: Vec<i64> = bars.iter().map(|b| b.time_unix).collect();
    let reset = ny_session_resets(&times, ANCHOR_HOUR);
    let vwap = vwap_session(bars, &volume, &reset);

    for i in 1..n {
        let v1 = vwap[i - 1];
        if v1.is_nan() || v1 == 0.0 {
            continue;
        }
        let dev = (bars[i - 1].close - v1) / v1;
        if dev <= -BAND {
            raw[i] = 1; // below VWAP -> revert up
        } else if dev >= BAND {
            raw[i] = -1; // above VWAP -> revert down
        }
    }
    raw
}

fn main() {
    run_with_csv("data/volume_fixture.csv", "VWAP-mean-reversion", vwap_mean_reversion);
}
