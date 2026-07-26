//! Frozen-benchmark runner (Rust side; the CANONICAL cross-arch golden source).
//! Each cell = ONE strategy x ONE dataset through the FULL ROLLING WALK-FORWARD
//! (per-window in-sample optimise, out-of-sample evaluate), aggregated over the
//! concatenated OOS stream, under BOTH cost regimes (NET realistic / GROSS
//! frictionless). Uses the engine's `classic_single_run` for the IS seed plus
//! the opt-in `walk_forward_collect` (returns the concatenated OOS stream +
//! aggregated Metrics instead of printing), and the cross-engine-PROVEN signal
//! set (engine-internal EMA default + the ewm-identical ema_cross / macd_zero).
//!
//! ONLY core=true strategies are emitted (atr_cross / rsi_revert / stoch_kd are
//! Python-only until item 5 ports their indicators).
//!
//! Constraint #4 honored: emits CSV via std::fs::write only; NO parquet, NO new
//! dependency. The manifest is parsed with a tiny hand-rolled reader (no toml
//! crate).
//!
//! Determinism: classic_single_run + walk_forward_collect are called directly
//! (never run_cfg), so no run_robustness_tests print noise; Monte Carlo is a
//! compile-time const, hard-seeded 42, print-only; cfg.sharpe_bar is forced
//! false (per-trade); walk_forward_collect runs the WFO loop with an EMPTY
//! scenario set, so the seeded INDICATOR_VARIANCE overlay never fires (the
//! harvested baseline rets_oos is overlay-independent anyway). The runner
//! ASSERTS each RUST-CONST manifest pin equals its compiled crate const and
//! aborts loudly on mismatch.
//!
//! Aggregated MaxDrawdown is taken on the OOS-only equity fraction (start 1.0
//! crypto / 0.0 forex), matching the Python runner, so net_mdd/gross_mdd agree.
//!
//! Run:
//!     cargo run --release --example benchmark_runner -- \
//!         --manifest tools/benchmark_manifest.toml --out /tmp/bench_rust.csv

use std::env;
use std::fs;
use std::path::Path;
use std::process::exit;

use quant_research_framework_rs::{
    classic_single_run, walk_forward_collect,
    default_ema_signal, load_ohlc, Bar, Config, RawSignalsFn, WfoOut,
    BACKTEST_CANDLES, DEFAULT_LB, MIN_TRADES, OPT_METRIC, OPTIMIZE_RRR,
    SMART_OPTIMIZATION, USE_WFO, FUNDING_FEE, WFO_TRIGGER_VAL,
};
// NOTE (change E, approved): the engine consts above plus the read-only
// `default_ema_signal`, `walk_forward_collect`, `WfoOut` must be `pub` for this
// runner. All are read-only / new opt-in surface (no behavior change to existing
// callers). See the engine-surface table in the package and src/lib.rs hunks in
// §(f). `compute_metrics_for` is also made pub (used INSIDE walk_forward_collect)
// but is NOT imported here, the runner reads the pre-aggregated WfoOut.agg.

// ---- signal library: ONLY the cross-engine-identical signals --------------
fn shifted(sig: Vec<i8>) -> Vec<i8> {
    if sig.is_empty() { return sig; }
    let n = sig.len();
    let mut out = vec![0i8; n];
    out[0] = sig[0];
    out[1..n].copy_from_slice(&sig[..n - 1]);
    out
}
fn ema(c: &[f64], span: usize) -> Vec<f64> {
    // ewm(span, adjust=False): o[0]=c[0], alpha=2/(span+1). Matches pandas
    // ewm(adjust=False) used by signal_ema_cross / signal_macd_zero -> identical.
    let a = 2.0 / (span as f64 + 1.0);
    let mut o = vec![0.0; c.len()];
    if c.is_empty() { return o; }
    o[0] = c[0];
    for i in 1..c.len() { o[i] = a * c[i] + (1.0 - a) * o[i - 1]; }
    o
}
fn signal_ema_cross(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let (f, s) = (ema(&c, lb), ema(&c, lb * 4));
    shifted((0..b.len()).map(|i|
        if f[i] > s[i] { 1 } else if f[i] < s[i] { -1 } else { 0 }).collect())
}
fn signal_macd_zero(b: &[Bar], lb: usize) -> Vec<i8> {
    let c: Vec<f64> = b.iter().map(|x| x.close).collect();
    let fp = lb.max(2);
    let sp = ((lb as f64 * 2.16).round() as usize).max(fp + 1);
    let gp = ((lb as f64 * 0.75).round() as usize).clamp(2, 9);
    let (fe, se) = (ema(&c, fp), ema(&c, sp));
    let macd: Vec<f64> = fe.iter().zip(se.iter()).map(|(a, b)| a - b).collect();
    let line = ema(&macd, gp);
    shifted((0..b.len()).map(|i|
        if macd[i] > line[i] { 1 } else if macd[i] < line[i] { -1 } else { 0 }).collect())
}
fn sig_for(id: &str) -> Option<RawSignalsFn> {
    match id {
        // engine_ema uses the engine's OWN default signal fn (the parity-
        // validated path), NOT a hand mirror, so the cross-engine assert and
        // the golden exercise the harness-proven signal.
        "engine_ema" => Some(default_ema_signal as RawSignalsFn),
        "ema_cross"  => Some(signal_ema_cross as RawSignalsFn),
        "macd_zero"  => Some(signal_macd_zero as RawSignalsFn),
        _ => None,   // atr_cross / rsi_revert / stoch_kd: Python-only
    }
}

// ---- tiny manifest reader (no toml crate; dep-light, constraint #4) --------
struct Dataset { id: String, path: String, kind: String, oos: Option<usize> }
struct Strat   { id: String, core: bool, enabled: bool }
struct Wfo { backtest: usize, oos: usize, use_oos2: bool, default_lb: usize,
             lb_lo: usize, lb_hi: usize, trigger_val: usize }
struct Fr { net_fee: f64, net_slip: f64, net_fund: f64, pip_def: f64, pip_jpy: f64 }
struct Pins { opt_metric: String, min_trades: usize, optimize_rrr: bool,
              smart_opt: bool, use_wfo: bool, use_regime_seg: bool }

fn num(v: &str) -> f64 {
    v.trim_start_matches([' ', '=']).split('#').next().unwrap().trim().parse().unwrap()
}
fn scalar(line: &str, key: &str) -> Option<String> {
    // match "key = ..." but not "key_other = ..."
    let head = line.split('=').next().unwrap().trim();
    if head != key { return None; }
    line.split('=').nth(1).map(|v|
        v.split('#').next().unwrap().trim().trim_matches('"').replace('_', ""))
}
fn strval(line: &str, key: &str) -> Option<String> {
    // like `scalar` but preserves underscores (id / path / kind strings)
    let head = line.split('=').next().unwrap().trim();
    if head != key { return None; }
    line.split('=').nth(1).map(|v|
        v.split('#').next().unwrap().trim().trim_matches('"').to_string())
}

fn parse_manifest(path: &str) -> (Wfo, Fr, Pins, Vec<Dataset>, Vec<Strat>) {
    let txt = fs::read_to_string(path).expect("manifest read");
    let mut wfo = Wfo { backtest: 10_000, oos: 90_000, use_oos2: false,
                        default_lb: 50, lb_lo: 12, lb_hi: 76, trigger_val: 5000 };
    let mut fr = Fr { net_fee: 0.02, net_slip: 0.03, net_fund: 0.01,
                      pip_def: 0.0001, pip_jpy: 0.01 };
    let mut pins = Pins { opt_metric: "Sharpe".into(), min_trades: 10,
                          optimize_rrr: true, smart_opt: true, use_wfo: true,
                          use_regime_seg: false };
    let (mut datasets, mut strats) = (Vec::new(), Vec::new());
    let mut sect = String::new();
    for raw in txt.lines() {
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') { continue; }
        if line.starts_with('[') {
            sect = line.to_string();
            if sect.starts_with("[[datasets]]") {
                datasets.push(Dataset { id: String::new(), path: String::new(), kind: String::new(), oos: None });
            } else if sect.starts_with("[[strategies]]") {
                strats.push(Strat { id: String::new(), core: true, enabled: true });
            }
            continue;
        }
        if let Some(v) = scalar(line, "opt_metric")    { pins.opt_metric = v; }
        if let Some(v) = scalar(line, "min_trades")    { pins.min_trades = v.parse().unwrap(); }
        if let Some(v) = scalar(line, "optimize_rrr")  { pins.optimize_rrr = v.contains("true"); }
        if let Some(v) = scalar(line, "smart_optimization") { pins.smart_opt = v.contains("true"); }
        if let Some(v) = scalar(line, "use_wfo")       { pins.use_wfo = v.contains("true"); }
        if let Some(v) = scalar(line, "use_regime_seg"){ pins.use_regime_seg = v.contains("true"); }
        if !sect.starts_with("[[") {  // global geometry only in [wfo]; per-dataset handled below
            if let Some(v) = scalar(line, "backtest_candles") { wfo.backtest = v.parse().unwrap(); }
            if let Some(v) = scalar(line, "oos_candles")   { wfo.oos = v.parse().unwrap(); }
        }
        if let Some(v) = scalar(line, "use_oos2")      { wfo.use_oos2 = v.contains("true"); }
        if let Some(v) = scalar(line, "default_lb")    { wfo.default_lb = v.parse().unwrap(); }
        if let Some(v) = scalar(line, "wfo_trigger_val") { wfo.trigger_val = v.parse().unwrap(); }
        if line.starts_with("lookback_range") {
            let inner: String = line.chars().filter(|c| c.is_ascii_digit() || *c == ',').collect();
            let mut it = inner.split(',');
            wfo.lb_lo = it.next().unwrap().parse().unwrap();
            wfo.lb_hi = it.next().unwrap().parse().unwrap();
        }
        if sect.starts_with("[frictions.net.crypto]") {
            if let Some(v) = line.strip_prefix("fee_pct")      { fr.net_fee  = num(v); }
            if let Some(v) = line.strip_prefix("slippage_pct") { fr.net_slip = num(v); }
            if let Some(v) = line.strip_prefix("funding_fee")  { fr.net_fund = num(v); }
        }
        if sect.starts_with("[frictions.net.fx]") {
            if let Some(v) = line.strip_prefix("pip_default")  { fr.pip_def = num(v); }
            if let Some(v) = line.strip_prefix("pip_jpy")      { fr.pip_jpy = num(v); }
        }
        if sect.starts_with("[[datasets]]") {
            if let Some(d) = datasets.last_mut() {
                if let Some(v) = strval(line, "id")   { d.id = v; }
                if let Some(v) = strval(line, "path") { d.path = v; }
                if let Some(v) = strval(line, "kind") { d.kind = v; }
                if let Some(v) = scalar(line, "oos_candles") { d.oos = v.parse().ok(); }
            }
        } else if sect.starts_with("[[strategies]]") {
            if let Some(s) = strats.last_mut() {
                if let Some(v) = strval(line, "id")      { s.id = v; }
                if let Some(v) = strval(line, "core")    { s.core = v.contains("true"); }
                if let Some(v) = strval(line, "enabled") { s.enabled = v.contains("true"); }
            }
        }
    }
    (wfo, fr, pins, datasets, strats)
}

/// Loud assert: each RUST-CONST manifest pin must equal its compiled crate
/// const, else a manifest re-pin would silently desync the engines. Runs
/// BEFORE the GROSS run zeroes cfg.funding_fee, so it asserts the NET pin.
fn assert_pins_match_consts(wfo: &Wfo, fr: &Fr, pins: &Pins) {
    let mut bad: Vec<String> = Vec::new();
    macro_rules! chk {
        ($name:expr, $manifest:expr, $konst:expr) => {
            if $manifest != $konst {
                bad.push(format!("  {} : manifest={:?} crate-const={:?}",
                                 $name, $manifest, $konst));
            }
        };
    }
    chk!("backtest_candles", wfo.backtest, BACKTEST_CANDLES);
    chk!("default_lb", wfo.default_lb, DEFAULT_LB);
    chk!("min_trades", pins.min_trades, MIN_TRADES);
    chk!("optimize_rrr", pins.optimize_rrr, OPTIMIZE_RRR);
    chk!("smart_optimization", pins.smart_opt, SMART_OPTIMIZATION);
    chk!("use_wfo", pins.use_wfo, USE_WFO);
    chk!("use_regime_seg", pins.use_regime_seg, false);
    chk!("opt_metric", pins.opt_metric.as_str(), OPT_METRIC);
    chk!("wfo_trigger_val", wfo.trigger_val, WFO_TRIGGER_VAL);
    chk!("crypto.funding_fee (NET)", fr.net_fund, FUNDING_FEE);
    if !bad.is_empty() {
        eprintln!("[benchmark_runner] FATAL: manifest pins diverge from compiled \
                   crate consts (a manifest re-pin needs an engine edit+rebuild):");
        for b in &bad { eprintln!("{}", b); }
        exit(3);
    }
}

fn fmt_or_na(v: f64) -> String {
    if v.is_finite() { format!("{:.4}", v) } else { "nan".into() }
}

fn run_cell(bars: &[Bar], mut cfg: Config, sig: RawSignalsFn)
    -> (f64, f64, f64, f64, usize, usize) {
    // IS seed + full rolling WFO; walk_forward_collect already returns the
    // aggregated Metrics (agg) built by compute_metrics_for on the concatenated
    // OOS stream + OOS-only equity fraction, plus the per-window OOS metrics.
    let base = classic_single_run(bars, &mut cfg, "bench", sig);
    let w: WfoOut = walk_forward_collect(bars, &base.eq_is_raw, &mut cfg, "bench", sig);
    (w.agg.roi, w.agg.sharpe, w.agg.pf, w.agg.max_drawdown,
     w.all_oos_rets.len(), w.per_window_oos.len())
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let mut manifest = "tools/benchmark_manifest.toml".to_string();
    let mut out = "/tmp/bench_rust.csv".to_string();
    let mut it = args.iter().skip(1);
    while let Some(a) = it.next() {
        match a.as_str() {
            "--manifest" => manifest = it.next().cloned().unwrap_or(manifest),
            "--out"      => out = it.next().cloned().unwrap_or(out),
            _ => {}
        }
    }
    let (wfo, fr, pins, datasets, strats) = parse_manifest(&manifest);
    assert_pins_match_consts(&wfo, &fr, &pins);

    // Single unbroken header literal (no line-continuation -> no stray spaces in
    // field names, so the Python DictReader keys match exactly).
    let mut lines: Vec<String> = vec![
        "dataset,strategy,core,engine,item5_target,windows,eff_oos_bars,net_roi,net_sharpe,net_pf,net_mdd,net_oos_disp,gross_roi,gross_sharpe,gross_pf,gross_mdd".into()];

    for ds in &datasets {
        if !Path::new(&ds.path).exists() {
            eprintln!("[benchmark_runner] FATAL: dataset {} missing: {}", ds.id, ds.path);
            exit(2);
        }
        let bars = load_ohlc(&ds.path);
        for st in &strats {
            if !st.enabled || !st.core { continue; }      // cross-engine core only
            let sig = match sig_for(&st.id) { Some(s) => s, None => continue };

            // ---- NET (realistic) ----
            let mut cfg_net = Config::new();
            // IS window is the compile-time const BACKTEST_CANDLES (asserted ==
            // manifest backtest_candles); not a Config field, so not set here.
            let oos_for_ds = ds.oos.unwrap_or(wfo.oos);  // per-dataset geometry override
            cfg_net.oos_candles = if wfo.use_oos2 { oos_for_ds * 2 } else { oos_for_ds };
            cfg_net.use_oos2 = wfo.use_oos2;
            cfg_net.sharpe_bar = false;
            if ds.kind == "fx" {
                cfg_net = cfg_net.with_forex_defaults();
                cfg_net.pip_size = if ds.path.to_uppercase().contains("JPY")
                    { fr.pip_jpy } else { fr.pip_def };
            } else {
                cfg_net.fee_pct = fr.net_fee;
                cfg_net.slippage_pct = fr.net_slip;
                cfg_net.funding_fee = fr.net_fund;        // opt-in Config field (==FUNDING_FEE)
            }
            let (n_roi, n_sh, n_pf, n_mdd, n_oos, n_win) = run_cell(&bars, cfg_net, sig);

            // ---- GROSS (frictionless) ----
            let mut cfg_g = Config::new();
            cfg_g.oos_candles = if wfo.use_oos2 { oos_for_ds * 2 } else { oos_for_ds };
            cfg_g.use_oos2 = wfo.use_oos2;
            cfg_g.sharpe_bar = false;
            if ds.kind == "fx" {
                cfg_g = cfg_g.with_forex_defaults();
                cfg_g.pip_size = if ds.path.to_uppercase().contains("JPY")
                    { fr.pip_jpy } else { fr.pip_def };
                cfg_g.slippage_pct = 0.0;
            } else {
                cfg_g.fee_pct = 0.0;
                cfg_g.slippage_pct = 0.0;
                cfg_g.funding_fee = 0.0;                  // opt-in field zeroes funding
            }
            let (g_roi, g_sh, g_pf, g_mdd, _, _) = run_cell(&bars, cfg_g, sig);

            // Rust does not compute across-window dispersion here (Python owns
            // the leaderboard dispersion column); emit n/a so the cross-engine
            // comparator skips it. `windows` IS emitted for the geometry audit.
            lines.push(format!(
                "{},{},1,both,0,{},{},{},{},{},{},n/a,{},{},{},{}",
                ds.id, st.id, n_win, n_oos,
                fmt_or_na(n_roi), fmt_or_na(n_sh), fmt_or_na(n_pf), fmt_or_na(n_mdd),
                fmt_or_na(g_roi), fmt_or_na(g_sh), fmt_or_na(g_pf), fmt_or_na(g_mdd),
            ));
        }
    }
    fs::write(&out, lines.join("\n") + "\n").expect("write csv");
    eprintln!("[benchmark_runner] wrote {} ({} cells)", out, lines.len() - 1);
}
