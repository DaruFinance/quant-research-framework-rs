use std::env;
use std::fs;

use quant_research_framework_rs::{run_trade_monte_carlo, MonteCarloMode};

fn value(args: &[String], name: &str) -> Result<String, String> {
    let index = args
        .iter()
        .position(|value| value == name)
        .ok_or_else(|| format!("missing {name}"))?;
    args.get(index + 1)
        .cloned()
        .ok_or_else(|| format!("missing value after {name}"))
}

fn parse_returns(path: &str) -> Result<Vec<f64>, String> {
    let text = fs::read_to_string(path).map_err(|error| format!("cannot read {path}: {error}"))?;
    let trimmed = text.trim();
    let body = if trimmed.starts_with('[') && trimmed.ends_with(']') {
        &trimmed[1..trimmed.len() - 1]
    } else {
        trimmed
    };
    body.split(|character: char| character == ',' || character.is_whitespace())
        .filter(|item| !item.is_empty())
        .map(|item| {
            item.parse::<f64>()
                .map_err(|error| format!("invalid return {item:?}: {error}"))
        })
        .collect()
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

fn run() -> Result<(), String> {
    let args = env::args().collect::<Vec<_>>();
    let mode = MonteCarloMode::parse(&value(&args, "--mode")?)?;
    if mode == MonteCarloMode::BarPermutation {
        return Err("bar-permutation requires the full queue coordinator".into());
    }
    let runs = value(&args, "--runs")?
        .parse::<usize>()
        .map_err(|error| error.to_string())?;
    let seed = value(&args, "--seed")?
        .parse::<u64>()
        .map_err(|error| error.to_string())?;
    let use_forex = matches!(value(&args, "--forex")?.as_str(), "true" | "1");
    let returns_path = value(&args, "--returns")?;
    let output = value(&args, "--output")?;
    let returns = parse_returns(&returns_path)?;
    let result = run_trade_monte_carlo(&returns, mode, runs, seed, use_forex)?;
    let mut metrics = String::new();
    for (index, metric) in result.metrics.iter().enumerate() {
        if index > 0 {
            metrics.push_str(",\n");
        }
        metrics.push_str(&format!(
            "    \"{}\": {{\"actual\": {}, \"percentile_midrank\": {:.17}, \"less\": {}, \"ties\": {}}}",
            metric.name, json_float(metric.actual), metric.percentile_midrank,
            metric.less, metric.ties,
        ));
    }
    let text = format!(
        "{{\n  \"mode\": \"{}\",\n  \"seed\": {},\n  \"requested_runs\": {},\n  \"completed_runs\": {},\n  \"sharpe_convention\": \"{}\",\n  \"drawdown_convention\": \"{}\",\n  \"metrics\": {{\n{}\n  }}\n}}\n",
        mode.as_str(), result.seed, result.requested_runs, result.completed_runs,
        result.sharpe_convention, result.drawdown_convention, metrics,
    );
    fs::write(output, text).map_err(|error| error.to_string())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
