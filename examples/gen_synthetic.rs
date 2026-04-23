//! Generate a synthetic OHLC CSV so you can smoke-test the backtester
//! without needing real market data or network access.
//!
//! Close prices follow a geometric Brownian motion (drift + vol); each
//! bar's open/high/low are derived from close with small random
//! perturbations that respect `high >= {open, close} >= low`. The output
//! CSV format matches what the Python sibling's `binance_ohlc_downloader.py`
//! emits, i.e. the columns `time,open,high,low,close` where `time` is
//! UNIX seconds (UTC). Any CSV either tool writes can be read by either.
//!
//! Usage:
//!   cargo run --release --example gen_synthetic                      # → data/SYNTHETIC.csv
//!   cargo run --release --example gen_synthetic -- --bars 100000     # longer series
//!   cargo run --release --example gen_synthetic -- --interval 30m    # 30-minute bars
//!   cargo run --release --example gen_synthetic -- --out data/foo.csv
//!   cargo run --release --example gen_synthetic -- --seed 7          # deterministic
//!
//! Then run either binary on the result:
//!   cargo run --release -- data/SYNTHETIC.csv
//!   cargo run --release --example atr_cross -- data/SYNTHETIC.csv

use std::fs::{create_dir_all, File};
use std::io::{BufWriter, Write};
use std::path::Path;

use rand::rngs::StdRng;
use rand::{Rng, SeedableRng};

fn interval_seconds(label: &str) -> Option<i64> {
    Some(match label {
        "1m"  =>     60,
        "5m"  =>    300,
        "15m" =>    900,
        "30m" =>   1800,
        "1h"  =>   3600,
        "4h"  =>  14400,
        "1d"  =>  86400,
        _ => return None,
    })
}

/// Two-point Box-Muller so we don't need rand_distr just for a normal.
fn normal(rng: &mut StdRng, mu: f64, sigma: f64) -> f64 {
    // Guard against the (vanishing) chance of u1 == 0.
    let u1: f64 = {
        let mut v: f64 = rng.gen();
        while v <= f64::EPSILON {
            v = rng.gen();
        }
        v
    };
    let u2: f64 = rng.gen();
    let z = (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos();
    mu + sigma * z
}

struct Args {
    bars: usize,
    interval: String,
    out: String,
    seed: u64,
    start_price: f64,
    start_unix: i64,
}

impl Default for Args {
    fn default() -> Self {
        Self {
            bars: 50_000,
            interval: "1h".to_string(),
            out: "data/SYNTHETIC.csv".to_string(),
            seed: 42,
            start_price: 100.0,
            start_unix: 1_600_000_000,
        }
    }
}

fn parse_args() -> Args {
    let mut args = Args::default();
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < argv.len() {
        let flag = argv[i].as_str();
        let val = || {
            argv.get(i + 1)
                .unwrap_or_else(|| panic!("{} needs a value", flag))
                .clone()
        };
        match flag {
            "--bars"        => { args.bars = val().parse().expect("bad --bars");        i += 2; }
            "--interval"    => { args.interval = val();                                 i += 2; }
            "--out"         => { args.out = val();                                      i += 2; }
            "--seed"        => { args.seed = val().parse().expect("bad --seed");        i += 2; }
            "--start-price" => { args.start_price = val().parse().expect("bad price");  i += 2; }
            "--start-unix"  => { args.start_unix = val().parse().expect("bad unix");    i += 2; }
            "-h" | "--help" => {
                eprintln!("{}", include_str!("gen_synthetic.rs").lines()
                    .take_while(|l| l.starts_with("//!"))
                    .map(|l| l.trim_start_matches("//!").trim_start())
                    .collect::<Vec<_>>().join("\n"));
                std::process::exit(0);
            }
            other => panic!("unknown flag: {}", other),
        }
    }
    args
}

fn main() {
    let args = parse_args();
    let interval_s = interval_seconds(&args.interval)
        .unwrap_or_else(|| panic!("unknown --interval {} (try 1m/5m/15m/30m/1h/4h/1d)", args.interval));

    let mut rng = StdRng::seed_from_u64(args.seed);
    let n = args.bars;
    let drift = 0.00002_f64;
    let vol = 0.01_f64;

    // Close path via GBM.
    let mut close = vec![0.0_f64; n];
    let mut log_price = args.start_price.ln();
    for i in 0..n {
        log_price += normal(&mut rng, drift, vol);
        close[i] = log_price.exp();
    }

    // Open: first bar seeds at start_price, subsequent bars gap from prev close.
    let mut open = vec![0.0_f64; n];
    open[0] = args.start_price;
    for i in 1..n {
        let gap = normal(&mut rng, 0.0, vol * 0.25);
        open[i] = close[i - 1] * (1.0 + gap);
    }

    if let Some(parent) = Path::new(&args.out).parent() {
        if !parent.as_os_str().is_empty() {
            create_dir_all(parent).expect("create output dir");
        }
    }
    let file = File::create(&args.out).expect("create output file");
    let mut w = BufWriter::new(file);
    writeln!(w, "time,open,high,low,close").unwrap();
    for i in 0..n {
        let body_hi = open[i].max(close[i]);
        let body_lo = open[i].min(close[i]);
        let wick_up = normal(&mut rng, 0.0, vol * 0.8).abs();
        let wick_dn = normal(&mut rng, 0.0, vol * 0.8).abs();
        let high = body_hi * (1.0 + wick_up);
        let low = body_lo * (1.0 - wick_dn);
        let t = args.start_unix + (i as i64) * interval_s;
        writeln!(w, "{},{:.8},{:.8},{:.8},{:.8}", t, open[i], high, low, close[i]).unwrap();
    }

    println!("Wrote {} bars ({}) to {}", args.bars, args.interval, args.out);
    println!("Next: cargo run --release -- {}", args.out);
}
