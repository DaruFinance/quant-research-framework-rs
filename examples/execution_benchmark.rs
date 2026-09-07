//! Matched execution-kernel benchmark used by `tools/bench_corrected.py`.
//!
//! This is deliberately narrower than the framework's WFO benchmark. It
//! consumes precomputed causal event codes so QRF, vectorbt and Backtesting.py
//! execute the same long-only orders over the same bars.

use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use quant_research_framework_rs::{benchmark_backtest, load_ohlc, Config, Trade};
use sha2::{Digest, Sha256};

fn load_events(path: &Path, expected_times: &[i64]) -> (Vec<String>, Vec<Vec<i8>>) {
    let file = File::open(path).expect("Cannot open event CSV");
    let mut lines = BufReader::new(file).lines();
    let header = lines
        .next()
        .expect("Event CSV is empty")
        .expect("Cannot read event header");
    let names: Vec<String> = header
        .trim_end()
        .split(',')
        .skip(1)
        .map(str::to_owned)
        .collect();
    assert_eq!(names.len(), 10, "Event CSV must contain 10 configurations");
    let mut events: Vec<Vec<i8>> = names
        .iter()
        .map(|_| Vec::with_capacity(expected_times.len()))
        .collect();

    for (row, line) in lines.enumerate() {
        let line = line.expect("Cannot read event row");
        let mut fields = line.trim_end().split(',');
        let timestamp: i64 = fields
            .next()
            .expect("Missing event timestamp")
            .parse()
            .unwrap();
        assert_eq!(
            timestamp, expected_times[row],
            "Event timestamp mismatch at row {row}"
        );
        for column in &mut events {
            let code: i8 = fields.next().expect("Missing event code").parse().unwrap();
            assert!(
                matches!(code, 0 | 1 | 2),
                "Long-only event code must be 0, 1 or 2"
            );
            column.push(code);
        }
        assert!(
            fields.next().is_none(),
            "Unexpected event column at row {row}"
        );
    }
    assert_eq!(events.first().map(Vec::len), Some(expected_times.len()));
    for (name, codes) in names.iter().zip(&events) {
        assert_eq!(codes.first(), Some(&0), "{name} opens on the first bar");
        let mut is_long = false;
        for &code in codes {
            match code {
                1 => {
                    assert!(!is_long, "{name} repeats a long entry");
                    is_long = true;
                }
                2 => {
                    assert!(is_long, "{name} closes while flat");
                    is_long = false;
                }
                _ => {}
            }
        }
        assert!(!is_long, "{name} does not close at the final open");
        assert_ne!(codes.last(), Some(&1), "{name} opens on the final bar");
    }
    (names, events)
}

fn write_ledger(path: &Path, name: &str, trades: &[Trade]) {
    let file = File::create(path).expect("Cannot create normalized ledger");
    let mut writer = BufWriter::new(file);
    writeln!(writer, "strategy,trade_id,side,entry_idx,exit_idx,entry_price,exit_price,quantity,gross_pnl,execution_charge,net_pnl").unwrap();
    for (trade_id, trade) in trades.iter().enumerate() {
        assert_eq!(trade.side, 1, "Execution benchmark is long-only");
        assert!(trade.qty > 0.0);
        writeln!(
            writer,
            "{name},{trade_id},1,{},{},{},{},1,{},{},{}",
            trade.entry_idx,
            trade.exit_idx,
            trade.entry_price,
            trade.exit_price,
            trade.gross_pnl / trade.qty,
            trade.fee / trade.qty,
            trade.net_pnl / trade.qty,
        )
        .expect("Cannot write normalized ledger row");
    }
    writer.flush().expect("Cannot flush normalized ledger");
}

fn sha256_bytes(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn sha256_file(path: &Path) -> String {
    let bytes = fs::read(path).expect("Cannot hash normalized ledger");
    sha256_bytes(&bytes)
}

fn lookback(name: &str) -> usize {
    name.split("_lb")
        .nth(1)
        .and_then(|tail| tail.split('_').next())
        .and_then(|value| value.parse().ok())
        .expect("Strategy name must contain _lb<integer>")
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let csv = args
        .get(1)
        .expect("usage: execution_benchmark OHLC EVENT_CSV OUT_DIR [BARS]");
    let event_path = Path::new(
        args.get(2)
            .expect("usage: execution_benchmark OHLC EVENT_CSV OUT_DIR [BARS]"),
    );
    let out = PathBuf::from(
        args.get(3)
            .expect("usage: execution_benchmark OHLC EVENT_CSV OUT_DIR [BARS]"),
    );
    let expected_bars = args
        .get(4)
        .map(|value| value.parse::<usize>().expect("BARS must be an integer"))
        .unwrap_or(150_000);
    fs::create_dir(&out).expect("Output directory must not already exist");
    let ledger_dir = out.join("ledgers");
    fs::create_dir(&ledger_dir).unwrap();

    let process_start = Instant::now();
    let mut bars = load_ohlc(csv);
    bars.truncate(expected_bars);
    assert_eq!(
        bars.len(),
        expected_bars,
        "Input contains fewer rows than requested"
    );
    let times: Vec<i64> = bars.iter().map(|bar| bar.time_unix).collect();
    let (names, events) = load_events(event_path, &times);

    let mut cfg = Config::new();
    cfg.fee_pct = 0.07;
    cfg.slippage_pct = 0.0;
    cfg.funding_fee = 0.0;
    cfg.use_tp = false;
    cfg.use_sessions = false;
    cfg.use_regime_seg = false;
    cfg.max_hold_bars = 0;

    let mut outputs = Vec::with_capacity(names.len());
    for (name, codes) in names.iter().zip(&events) {
        let event_hash = sha256_bytes(&codes.iter().map(|value| *value as u8).collect::<Vec<_>>());
        let event_count = codes.iter().filter(|&&value| value != 0).count();
        let started = Instant::now();
        let (trades, _, _, _) = benchmark_backtest(&bars, codes, &cfg);
        let elapsed = started.elapsed().as_secs_f64();
        outputs.push((name, event_hash, event_count, elapsed, trades));
    }
    let engine_elapsed: f64 = outputs.iter().map(|(_, _, _, elapsed, _)| elapsed).sum();

    for (name, _, _, _, trades) in &outputs {
        write_ledger(&ledger_dir.join(format!("{name}.csv")), name, trades);
    }

    let results = File::create(out.join("results.json")).expect("Cannot create results JSON");
    let mut results = BufWriter::new(results);
    writeln!(results, "{{").unwrap();
    writeln!(results, "  \"engine\": \"qrf-rust\",").unwrap();
    writeln!(
        results,
        "  \"versions\": {{\"quant-research-framework-rs\": \"{}\"}},",
        env!("CARGO_PKG_VERSION")
    )
    .unwrap();
    writeln!(results, "  \"bars\": {},", bars.len()).unwrap();
    writeln!(results, "  \"is_smoke\": {},", bars.len() != 150_000).unwrap();
    writeln!(results, "  \"csv\": \"{}\",", csv).unwrap();
    writeln!(results, "  \"events\": \"{}\",", event_path.display()).unwrap();
    writeln!(
        results,
        "  \"events_file_sha256\": \"{}\",",
        sha256_file(event_path)
    )
    .unwrap();
    writeln!(results, "  \"strategies\": {},", outputs.len()).unwrap();
    writeln!(
        results,
        "  \"engine_elapsed_total_s\": {:.9},",
        engine_elapsed
    )
    .unwrap();
    writeln!(
        results,
        "  \"process_internal_wall_s\": {:.9},",
        process_start.elapsed().as_secs_f64()
    )
    .unwrap();
    writeln!(results, "  \"signal_semantics\": \"long-only: positive enters, negative closes, zero holds; causal next-bar-open events\",").unwrap();
    writeln!(results, "  \"boundary\": \"fresh process; one OHLC load and one frozen-event CSV load; event generation outside process and engine timer; ten serial full-history execution passes; ledger normalization outside engine timer\",").unwrap();
    writeln!(results, "  \"finalization\": \"suppress final-bar flip and explicitly close existing position at final-bar open\",").unwrap();
    writeln!(results, "  \"combined_execution_charge_per_fill\": 0.0007,").unwrap();
    writeln!(results, "  \"combined_execution_charge_note\": \"5 bp taker fee + 2 bp slippage proxy; zero price offset; not a price-slippage model\",").unwrap();
    writeln!(results, "  \"features_disabled\": [\"walk_forward\", \"optimization\", \"stop_loss\", \"take_profit\", \"funding\"],").unwrap();
    writeln!(results, "  \"strategy_name_note\": \"names are retained from the audited 10-config batch for traceability; embedded tp labels are not active in this workload\",").unwrap();
    writeln!(results, "  \"results\": [").unwrap();
    for (index, (name, event_hash, event_count, elapsed, trades)) in outputs.iter().enumerate() {
        let gross_per_unit: f64 = trades.iter().map(|trade| trade.gross_pnl / trade.qty).sum();
        let charge_per_unit: f64 = trades.iter().map(|trade| trade.fee / trade.qty).sum();
        let net_per_unit: f64 = trades.iter().map(|trade| trade.net_pnl / trade.qty).sum();
        let ledger = ledger_dir.join(format!("{name}.csv"));
        let suffix = if index + 1 == outputs.len() { "" } else { "," };
        writeln!(
            results,
            "    {{\"name\":\"{name}\",\"lookback\":{},\"event_sha256\":\"{}\",\"events\":{},\"trades\":{},\"engine_elapsed_s\":{elapsed:.9},\"ledger\":\"{}\",\"ledger_sha256\":\"{}\",\"gross_pnl\":{gross_per_unit:.17},\"execution_charge\":{charge_per_unit:.17},\"net_pnl\":{net_per_unit:.17}}}{suffix}",
            lookback(name),
            event_hash,
            event_count,
            trades.len(),
            ledger.display(),
            sha256_file(&ledger),
        )
        .unwrap();
    }
    writeln!(results, "  ]").unwrap();
    writeln!(results, "}}").unwrap();
    results.flush().expect("Cannot flush results JSON");
}
