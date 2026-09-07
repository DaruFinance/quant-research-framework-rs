//! Reproducible full-scale WFO benchmark runner.
//!
//! Runs 10 fixed configurations sequentially over exactly 150,000 bars,
//! covering 10,000 IS bars and 140,000 OOS bars in 28 windows. Compilation is
//! excluded from timing by `tools/bench_paper.py`.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::time::Instant;

use quant_research_framework_rs::indicators::{
    compute_atr, compute_rsi, compute_sma, compute_stoch,
};
use quant_research_framework_rs::{load_ohlc, walk_forward_collect, Bar, Config, RawSignalsFn};
use sha2::{Digest, Sha256};

fn shifted(signal: Vec<i8>) -> Vec<i8> {
    if signal.is_empty() {
        return signal;
    }
    let mut out = vec![0; signal.len()];
    out[0] = signal[0];
    out[1..].copy_from_slice(&signal[..signal.len() - 1]);
    out
}

fn ema(close: &[f64], span: usize) -> Vec<f64> {
    let alpha = 2.0 / (span as f64 + 1.0);
    let mut out = vec![0.0; close.len()];
    if close.is_empty() {
        return out;
    }
    out[0] = close[0];
    for index in 1..close.len() {
        out[index] = if out[index - 1] == close[index] {
            close[index]
        } else {
            alpha * close[index] + (1.0 - alpha) * out[index - 1]
        };
    }
    out
}

fn signal_ema_cross(bars: &[Bar], lookback: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|bar| bar.close).collect();
    let fast = ema(&close, lookback);
    let slow = ema(&close, lookback * 4);
    shifted(
        fast.iter()
            .zip(&slow)
            .map(|(fast, slow)| {
                if fast > slow {
                    1
                } else if fast < slow {
                    -1
                } else {
                    0
                }
            })
            .collect(),
    )
}

fn signal_atr_cross(bars: &[Bar], lookback: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|bar| bar.close).collect();
    let high: Vec<f64> = bars.iter().map(|bar| bar.high).collect();
    let low: Vec<f64> = bars.iter().map(|bar| bar.low).collect();
    let fast = compute_sma(&close, lookback);
    let slow = compute_sma(&close, lookback * 4);
    let rsi = compute_rsi(&close, lookback);
    let atr = compute_atr(&high, &low, &close, lookback);
    let mut signal = vec![0; bars.len()];
    for index in 0..bars.len() {
        if fast[index].is_finite()
            && slow[index].is_finite()
            && rsi[index].is_finite()
            && atr[index].is_finite()
        {
            if fast[index] > slow[index] + atr[index] && rsi[index] >= 50.0 {
                signal[index] = 1;
            } else if fast[index] < slow[index] - atr[index] && rsi[index] <= 50.0 {
                signal[index] = -1;
            }
        }
    }
    shifted(signal)
}

fn signal_macd_zero(bars: &[Bar], lookback: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|bar| bar.close).collect();
    let fast_period = lookback.max(2);
    let slow_period = ((lookback as f64 * 2.16).round() as usize).max(fast_period + 1);
    let signal_period = ((lookback as f64 * 0.75).round() as usize).clamp(2, 9);
    let fast = ema(&close, fast_period);
    let slow = ema(&close, slow_period);
    let macd: Vec<f64> = fast
        .iter()
        .zip(&slow)
        .map(|(fast, slow)| fast - slow)
        .collect();
    let line = ema(&macd, signal_period);
    shifted(
        macd.iter()
            .zip(&line)
            .map(|(macd, line)| {
                if macd > line {
                    1
                } else if macd < line {
                    -1
                } else {
                    0
                }
            })
            .collect(),
    )
}

fn signal_rsi_revert(bars: &[Bar], lookback: usize) -> Vec<i8> {
    let close: Vec<f64> = bars.iter().map(|bar| bar.close).collect();
    let rsi = compute_rsi(&close, lookback);
    let signal = rsi
        .iter()
        .map(|value| {
            if value.is_finite() && *value < 35.0 {
                1
            } else if value.is_finite() && *value > 65.0 {
                -1
            } else {
                0
            }
        })
        .collect();
    shifted(signal)
}

fn signal_stoch_kd(bars: &[Bar], lookback: usize) -> Vec<i8> {
    let high: Vec<f64> = bars.iter().map(|bar| bar.high).collect();
    let low: Vec<f64> = bars.iter().map(|bar| bar.low).collect();
    let close: Vec<f64> = bars.iter().map(|bar| bar.close).collect();
    let stoch = compute_stoch(&high, &low, &close, lookback);
    let signal = stoch
        .iter()
        .map(|value| {
            if value.is_finite() && *value < 20.0 {
                1
            } else if value.is_finite() && *value > 80.0 {
                -1
            } else {
                0
            }
        })
        .collect();
    shifted(signal)
}

macro_rules! fixed {
    ($name:ident, $base:ident, $lookback:expr) => {
        fn $name(bars: &[Bar], _: usize) -> Vec<i8> {
            $base(bars, $lookback)
        }
    };
}

fixed!(ema14, signal_ema_cross, 14);
fixed!(ema40, signal_ema_cross, 40);
fixed!(atr20, signal_atr_cross, 20);
fixed!(atr50, signal_atr_cross, 50);
fixed!(macd12, signal_macd_zero, 12);
fixed!(macd26, signal_macd_zero, 26);
fixed!(rsi14, signal_rsi_revert, 14);
fixed!(rsi28, signal_rsi_revert, 28);
fixed!(stoch14, signal_stoch_kd, 14);
fixed!(stoch21, signal_stoch_kd, 21);

struct Spec {
    name: &'static str,
    family: &'static str,
    lookback: usize,
    tp: f64,
    signal: RawSignalsFn,
}

fn specs() -> [Spec; 10] {
    [
        Spec {
            name: "ema_cross_lb14_tp2.0",
            family: "ema_cross",
            lookback: 14,
            tp: 2.0,
            signal: ema14,
        },
        Spec {
            name: "ema_cross_lb40_tp4.5",
            family: "ema_cross",
            lookback: 40,
            tp: 4.5,
            signal: ema40,
        },
        Spec {
            name: "atr_cross_lb20_tp1.6",
            family: "atr_cross",
            lookback: 20,
            tp: 1.6,
            signal: atr20,
        },
        Spec {
            name: "atr_cross_lb50_tp2.0",
            family: "atr_cross",
            lookback: 50,
            tp: 2.0,
            signal: atr50,
        },
        Spec {
            name: "macd_zero_lb12_tp3.0",
            family: "macd_zero",
            lookback: 12,
            tp: 3.0,
            signal: macd12,
        },
        Spec {
            name: "macd_zero_lb26_tp3.0",
            family: "macd_zero",
            lookback: 26,
            tp: 3.0,
            signal: macd26,
        },
        Spec {
            name: "rsi_revert_lb14_tp0.5",
            family: "rsi_revert",
            lookback: 14,
            tp: 0.5,
            signal: rsi14,
        },
        Spec {
            name: "rsi_revert_lb28_tp2.0",
            family: "rsi_revert",
            lookback: 28,
            tp: 2.0,
            signal: rsi28,
        },
        Spec {
            name: "stoch_kd_lb14_tp0.8",
            family: "stoch_kd",
            lookback: 14,
            tp: 0.8,
            signal: stoch14,
        },
        Spec {
            name: "stoch_kd_lb21_tp1.2",
            family: "stoch_kd",
            lookback: 21,
            tp: 1.2,
            signal: stoch21,
        },
    ]
}

fn json_number(value: f64) -> String {
    if value.is_finite() {
        format!("{value:.17}")
    } else {
        "null".to_owned()
    }
}

fn sha256_file(path: &std::path::Path) -> String {
    let bytes = fs::read(path).expect("Cannot hash ledger");
    format!("{:x}", Sha256::digest(bytes))
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let csv = args.get(1).expect("usage: wfo_benchmark OHLC OUT_DIR");
    let out = PathBuf::from(args.get(2).expect("usage: wfo_benchmark OHLC OUT_DIR"));
    fs::create_dir(&out).expect("Output directory must not already exist");
    let ledger_dir = out.join("ledgers");
    fs::create_dir(&ledger_dir).unwrap();

    let process_start = Instant::now();
    let bars = load_ohlc(csv);
    assert_eq!(
        bars.len(),
        150_000,
        "Benchmark requires exactly 150,000 bars"
    );
    let mut rows = Vec::new();

    for spec in specs() {
        let ledger = ledger_dir.join(format!("{}.csv", spec.name));
        let mut cfg = Config::new();
        cfg.export_path = ledger.to_string_lossy().into_owned();
        cfg.oos_candles = 140_000;
        cfg.tp_percentage = spec.tp;
        cfg.use_tp = true;
        cfg.fee_pct = 0.02;
        cfg.slippage_pct = 0.03;
        cfg.funding_fee = 0.01;
        cfg.sl_override = Some(1.0);
        assert_eq!(cfg.oos_candles, 140_000);
        assert_eq!(
            (
                cfg.fee_pct,
                cfg.slippage_pct,
                cfg.funding_fee,
                cfg.sl_override,
            ),
            (0.02, 0.03, 0.01, Some(1.0))
        );
        let started = Instant::now();
        let result = walk_forward_collect(&bars, &[], &mut cfg, spec.name, spec.signal);
        let elapsed = started.elapsed().as_secs_f64();
        assert_eq!(result.per_window_oos.len(), 28);
        let ledger_rows = fs::read_to_string(&ledger).unwrap().lines().count() - 1;
        rows.push(format!(
            "{{\"name\":\"{}\",\"family\":\"{}\",\"fixed_lb\":{},\"tp_input_pct\":{},\"windows\":28,\"elapsed_s\":{:.9},\"ledger\":\"{}\",\"ledger_rows\":{},\"ledger_sha256\":\"{}\",\"metrics\":{{\"trades\":{},\"roi\":{},\"pf\":{},\"sharpe\":{},\"max_drawdown\":{}}}}}",
            spec.name,
            spec.family,
            spec.lookback,
            spec.tp,
            elapsed,
            ledger.display(),
            ledger_rows,
            sha256_file(&ledger),
            result.agg.trades,
            json_number(result.agg.roi),
            json_number(result.agg.pf),
            json_number(result.agg.sharpe),
            json_number(result.agg.max_drawdown),
        ));
    }

    let output = format!(
        "{{\n  \"engine\": \"qrf-rust\",\n  \"processes\": 1,\n  \"strategies\": 10,\n  \"bars\": 150000,\n  \"is_bars\": 10000,\n  \"oos_bars\": 140000,\n  \"expected_windows_each\": 28,\n  \"boundary\": \"fresh process; one CSV load; serial WFO-only configurations; no classic, Monte Carlo, robustness, or warm-up\",\n  \"fixed_lb_note\": \"each wrapper ignores the engine candidate LB and evaluates its named fixed LB; the unchanged candidate search is redundant timed work\",\n  \"rrr_note\": \"dynamic RRR optimization remains enabled, so tp_input_pct is a traceability label rather than the selected OOS take-profit\",\n  \"process_internal_wall_s\": {:.9},\n  \"results\": [\n    {}\n  ]\n}}\n",
        process_start.elapsed().as_secs_f64(),
        rows.join(",\n    "),
    );
    fs::write(out.join("results.json"), output).expect("Cannot write results JSON");
}
