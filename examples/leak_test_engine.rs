//! Leak-test CLI wrapper around `classic_single_run`.
//!
//! Designed to be invoked repeatedly from `live_runner.py` on a growing
//! CSV prefix, and once on the full CSV by `batch_runner.py`. Both
//! invocations pin the IS slice to the first 10_000 bars (the engine
//! const `BACKTEST_CANDLES`) by setting `cfg.oos_candles = N - 10_000`
//! at every call. This guarantees that live's optimizer-picked LB is
//! identical to batch's (the IS slice is byte-identical), so any
//! divergence in the OOS trade list is a real causality leak — not a
//! window-sliding artifact.
//!
//! Usage:
//!     leak_test_engine <csv_path> <out_trades_csv>
//!
//! Input CSV format: engine-native `time,open,high,low,close` (no asset
//! column). Single-asset only.
//!
//! Output: a copy of the engine's emitted `trade_list.csv` placed at the
//! requested output path. If the engine emits no trade list (e.g. the
//! optimizer found no usable LB, or N < 10_000 so IS is empty), this
//! writes a header-only file so consumers can rely on the file existing.

use std::env;
use std::fs;
use std::path::Path;

use quant_research_framework_rs::{
    classic_single_run, Bar, Config, RawSignalsFn,
};

// ---------------------------------------------------------------------------
// Strategy: SMA fast/slow cross. Engine's optimizer sweeps LB.
// ---------------------------------------------------------------------------
fn sma(close: &[f64], length: usize) -> Vec<f64> {
    let mut out = vec![f64::NAN; close.len()];
    let mut sum = 0.0;
    for i in 0..close.len() {
        sum += close[i];
        if i >= length { sum -= close[i - length]; }
        if i + 1 >= length { out[i] = sum / length as f64; }
    }
    out
}

fn signal_sma_cross(bars: &[Bar], lb: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|b| b.close).collect();
    let fast = sma(&close, lb);
    let slow = sma(&close, lb * 4);
    let mut sig = vec![0i8; bars.len()];
    for i in 0..bars.len() {
        if !fast[i].is_nan() && !slow[i].is_nan() {
            if fast[i] > slow[i] { sig[i] = 1; }
            else if fast[i] < slow[i] { sig[i] = -1; }
        }
    }
    // Engine expects signal-at-i to be the position-decision made at i
    // (it shifts internally via `drift_entries`). We emit the raw target
    // here. NOTE: `batch_runner.rs` in this repo uses a `shifted()` helper
    // to push by one bar; we do NOT shift here because the engine's
    // `parse_signals_for` + `drift_entries` already handle entry-bar
    // alignment. Whichever convention is used, it must be IDENTICAL in
    // batch and live calls — they call this same function via the same
    // binary, so consistency is guaranteed by construction.
    sig
}

// ---------------------------------------------------------------------------
// Minimal CSV loader (engine format: time,open,high,low,close)
// ---------------------------------------------------------------------------
fn load_bars(csv_path: &str) -> Vec<Bar> {
    use std::fs::File;
    use std::io::{BufRead, BufReader};
    let f = File::open(csv_path).unwrap_or_else(|_| panic!("Cannot open CSV: {}", csv_path));
    let reader = BufReader::new(f);
    let mut bars = Vec::new();
    for (i, line) in reader.lines().enumerate() {
        let line = line.unwrap();
        if i == 0 { continue; }
        let cols: Vec<&str> = line.split(',').collect();
        if cols.len() < 5 { continue; }
        bars.push(Bar {
            time_unix: cols[0].trim().parse().expect("bad time"),
            open:      cols[1].trim().parse().expect("bad open"),
            high:      cols[2].trim().parse().expect("bad high"),
            low:       cols[3].trim().parse().expect("bad low"),
            close:     cols[4].trim().parse().expect("bad close"),
        });
    }
    bars
}

// ---------------------------------------------------------------------------
// Driver
// ---------------------------------------------------------------------------
fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: leak_test_engine <csv_path> <out_trades_csv>");
        std::process::exit(2);
    }
    let csv_path = &args[1];
    let out_path = &args[2];

    let bars = load_bars(csv_path);
    let n = bars.len();

    // Pin IS to first 10_000 bars: cfg.oos_candles = N - 10_000.
    // Engine const BACKTEST_CANDLES = 10_000 (not configurable per-call).
    // If N <= 10_000, IS would be empty and the engine emits no trades —
    // write a header-only file and exit cleanly so the live driver can
    // proceed.
    const IS_SIZE: usize = 10_000;
    if n <= IS_SIZE {
        // Header-only stub so the consumer always has a file to read.
        fs::write(out_path,
            "strategy,window,sample,side,entry_time,open_entry,high_entry,low_entry,close_entry,exit_time,open_exit,high_exit,low_exit,close_exit,pnl\n"
        ).expect("write stub");
        return;
    }
    let oos_size = n - IS_SIZE;

    let mut cfg = Config::new();
    cfg.oos_candles  = oos_size;
    cfg.fee_pct      = 0.05;   // 0.05% taker per user spec
    cfg.slippage_pct = 0.02;   // 0.02% per fill per user spec

    let sig_fn: RawSignalsFn = signal_sma_cross;
    let _res = classic_single_run(&bars, &mut cfg, "leak_test", sig_fn);

    // classic_single_run writes `trade_list.csv` in CWD. Copy to caller's
    // requested path. If the engine emitted nothing (e.g. optimizer found
    // no best_lb), produce a header-only stub.
    let engine_export = Path::new("trade_list.csv");
    if engine_export.exists() {
        fs::copy(engine_export, out_path).expect("copy trade_list.csv");
    } else {
        fs::write(out_path,
            "strategy,window,sample,side,entry_time,open_entry,high_entry,low_entry,close_entry,exit_time,open_exit,high_exit,low_exit,close_exit,pnl\n"
        ).expect("write stub");
    }
}
