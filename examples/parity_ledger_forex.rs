//! Stable forex runner for tools/parity_ledger.py; no generated shared source.
use quant_research_framework_rs::{Config, default_ema_signal, load_ohlc, run_cfg};

fn main() {
    let csv = std::env::args().nth(1).expect("Supply an OHLC CSV path");
    let bars = load_ohlc(&csv);
    let cfg = Config::new().with_forex_defaults().with_pip_size_for(&csv);
    run_cfg(&bars, "EMA-crossover", default_ema_signal, cfg);
}
