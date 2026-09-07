use std::env;
use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::Path;
use std::time::Instant;

use quant_research_framework_rs::{
    default_ema_signal, load_ohlc, permute_bars, run_frozen_backtest, Config, Metrics, Trade,
};

fn value(args: &[String], name: &str) -> Result<String, String> {
    let index = args.iter().position(|value| value == name)
        .ok_or_else(|| format!("missing {name}"))?;
    args.get(index + 1)
        .cloned()
        .ok_or_else(|| format!("missing value after {name}"))
}

fn parse_bool(value: &str, name: &str) -> Result<bool, String> {
    match value {
        "true" | "1" => Ok(true),
        "false" | "0" => Ok(false),
        _ => Err(format!("{name} must be true or false")),
    }
}

fn json_float(value: f64) -> String {
    if value == f64::INFINITY {
        "\"Infinity\"".into()
    } else if value == f64::NEG_INFINITY {
        "\"-Infinity\"".into()
    } else if value.is_nan() {
        "\"NaN\"".into()
    } else {
        format!("{value:.17}")
    }
}

fn write_ledger(path: &Path, trades: &[Trade]) -> Result<(), String> {
    let file = File::create(path).map_err(|error| error.to_string())?;
    let mut writer = BufWriter::new(file);
    writer.write_all(b"QRFMCL01").map_err(|error| error.to_string())?;
    writer
        .write_all(&(trades.len() as u64).to_le_bytes())
        .map_err(|error| error.to_string())?;
    for trade in trades {
        writer.write_all(&trade.side.to_le_bytes()).map_err(|error| error.to_string())?;
        writer.write_all(&trade.entry_idx.to_le_bytes()).map_err(|error| error.to_string())?;
        writer.write_all(&trade.exit_idx.to_le_bytes()).map_err(|error| error.to_string())?;
        for value in [
            trade.entry_price, trade.exit_price, trade.qty, trade.net_pnl,
            trade.fee, trade.slippage, trade.funding, trade.gross_pnl,
        ] {
            writer.write_all(&value.to_le_bytes()).map_err(|error| error.to_string())?;
        }
    }
    writer.flush().map_err(|error| error.to_string())
}

fn write_metrics(path: &Path, seed: u64, bars: usize, metrics: &Metrics, elapsed: f64) -> Result<(), String> {
    let text = format!(
        concat!(
            "{{\n",
            "  \"seed\": {seed},\n",
            "  \"bars\": {bars},\n",
            "  \"trades\": {trades},\n",
            "  \"elapsed_seconds\": {elapsed:.9},\n",
            "  \"metrics\": {{\n",
            "    \"ROI\": {roi},\n",
            "    \"PF\": {pf},\n",
            "    \"WinRate\": {win_rate},\n",
            "    \"Exp\": {expectancy},\n",
            "    \"Sharpe\": {sharpe},\n",
            "    \"MaxDrawdown\": {max_drawdown},\n",
            "    \"Consistency\": {consistency}\n",
            "  }}\n",
            "}}\n"
        ),
        seed = seed,
        bars = bars,
        trades = metrics.trades,
        elapsed = elapsed,
        roi = json_float(metrics.roi),
        pf = json_float(metrics.pf),
        win_rate = json_float(metrics.win_rate),
        expectancy = json_float(metrics.exp),
        sharpe = json_float(metrics.sharpe),
        max_drawdown = json_float(metrics.max_drawdown),
        consistency = json_float(metrics.consistency),
    );
    fs::write(path, text).map_err(|error| error.to_string())
}

fn run() -> Result<(), String> {
    let args = env::args().collect::<Vec<_>>();
    let input = value(&args, "--input")?;
    let output = value(&args, "--output")?;
    let seed = value(&args, "--seed")?.parse::<u64>().map_err(|error| error.to_string())?;
    let strategy = value(&args, "--strategy")?;
    let lookback = value(&args, "--lookback")?.parse::<usize>().map_err(|error| error.to_string())?;
    if strategy != "ema-crossover" {
        return Err(format!(
            "unsupported strategy {strategy:?}; register a RawSignalsFn in a custom worker"
        ));
    }
    if lookback == 0 {
        return Err("lookback must be positive".into());
    }
    let output = Path::new(&output);
    fs::create_dir_all(output).map_err(|error| error.to_string())?;
    let mut cfg = Config::new();
    cfg.use_monte_carlo = false;
    cfg.fee_pct = value(&args, "--fee-pct")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.slippage_pct = value(&args, "--slippage-pct")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.funding_fee = value(&args, "--funding-fee")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.use_sl = parse_bool(&value(&args, "--use-sl")?, "--use-sl")?;
    cfg.sl_override = Some(value(&args, "--sl-percentage")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?);
    cfg.use_tp = parse_bool(&value(&args, "--use-tp")?, "--use-tp")?;
    cfg.tp_percentage = value(&args, "--tp-percentage")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.account_size = value(&args, "--account-size")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.position_size = value(&args, "--position-size")?.parse().map_err(|error: std::num::ParseFloatError| error.to_string())?;
    cfg.use_forex = parse_bool(&value(&args, "--forex")?, "--forex")?;
    cfg.sharpe_bar = value(&args, "--sharpe-mode")? == "bar";
    cfg.max_hold_bars = value(&args, "--max-hold-bars")?.parse().map_err(|error: std::num::ParseIntError| error.to_string())?;

    let started = Instant::now();
    let source = load_ohlc(&input);
    let bars = permute_bars(&source, seed)?;
    let result = run_frozen_backtest(&bars, &cfg, lookback, default_ema_signal)?;
    write_ledger(&output.join("ledger.bin"), &result.trades)?;
    write_metrics(
        &output.join("metrics.json"), seed, bars.len(), &result.metrics,
        started.elapsed().as_secs_f64(),
    )?;
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
