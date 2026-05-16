//! T6 (Carry & Basis) structural enumerator with 3-window WFO.
//!
//! The carry fixture is short (200 funding events ≈ 5 days at 8h cadence).
//! We split it into 3 sliding IS/OOS windows and evaluate each strategy's
//! signal-driven carry PnL on each OOS slice. Carry PnL at funding event
//! t is approximated as: signal_direction(t-1) * funding_rate(t) (i.e.,
//! you held the perp position into event t and pocketed the funding).
//!
//! Run with:
//!     cargo run --release --jobs 1 --features carry --example enumerate_t6 -- /tmp/t6_fix

#![cfg(feature = "carry")]

use std::fs;
use std::io::Write as _;
use std::path::PathBuf;
use std::time::Instant;

use quant_research_framework_rs::carry::{
    load_basis, load_funding, load_oi, BasisBlowoutTrigger,
    FundingMomentumModel, FundingOICointegrationModel, PersistentFundingSignModel,
};

#[derive(Clone, Copy)]
enum ModelKind { Persistent, Momentum, OICoint, BasisBlowout }

fn model_name(k: ModelKind) -> &'static str {
    match k {
        ModelKind::Persistent   => "persistent_sign",
        ModelKind::Momentum     => "momentum",
        ModelKind::OICoint      => "oi_cointegration",
        ModelKind::BasisBlowout => "basis_blowout",
    }
}

fn main() {
    let base = std::env::args().nth(1).unwrap_or_else(|| "/home/daru/leak_test/synth_data/t6_fix".to_string());
    let funding = load_funding(format!("{}/funding.csv", base), "binance_perp", true).expect("load_funding");
    let basis = load_basis(format!("{}/basis.csv", base), "btc_perp_spot", 0.01).expect("load_basis");
    let oi = load_oi(format!("{}/oi.csv", base), 3600, 60).expect("load_oi");
    let n = funding.events.len();
    eprintln!("loaded — funding_n={}, basis_n={}, oi_n={}", n, basis.records.len(), oi.records.len());

    // 3-window WFO on the funding event grid. Split into 6 equal slices,
    // windows shift by 1 slice each. IS = 2 slices, OOS = 1 slice.
    let slice = n / 6;
    let windows: Vec<(usize, usize, usize, usize)> = vec![
        (0,         2 * slice, 2 * slice, 3 * slice),
        (slice,     3 * slice, 3 * slice, 4 * slice),
        (2 * slice, 4 * slice, 4 * slice, 5 * slice),
    ];
    eprintln!("WFO geometry (slice={} events):", slice);
    for (i, w) in windows.iter().enumerate() {
        eprintln!("  W{:02}: IS [{}..{}]  OOS [{}..{}]", i + 1, w.0, w.1, w.2, w.3);
    }

    let out_root = PathBuf::from("/home/daru/leak_test/strategies/T6_synth");
    fs::create_dir_all(&out_root).unwrap();
    let index_path = out_root.join("INDEX.txt");
    let mut index = fs::File::create(&index_path).unwrap();
    writeln!(index, "# T6 enumeration — id,model,p1,p2,path,oos_pnl_W1,oos_pnl_W2,oos_pnl_W3,oos_pnl_total,n_oos_signals,elapsed_s").unwrap();

    let probe_times: Vec<i64> = funding.events.iter().map(|e| e.time_s).collect();
    let event_rates: Vec<f64> = funding.events.iter().map(|e| e.rate).collect();

    // Carry P&L convention: at funding event i, you've been holding `dir`
    // (= signal at event i-1) and you receive direction * rate(i).
    let pnl_at = |dir: i32, i: usize| -> f64 {
        if i == 0 || dir == 0 { 0.0 }
        else { dir as f64 * event_rates[i] }
    };

    let p_persistent: Vec<usize> = vec![1, 2, 3, 4, 5, 6, 8, 10];
    let p_momentum_window: Vec<usize> = vec![5, 10, 20, 30, 50];
    let p_momentum_k:      Vec<f64>   = vec![0.5, 1.0, 1.5, 2.0, 2.5];
    let p_coint_window:    Vec<usize> = vec![5, 10, 20, 30, 50];
    let p_coint_k:         Vec<f64>   = vec![0.5, 1.0, 1.5, 2.0, 2.5];
    let p_blowout_k:       Vec<f64>   = vec![1.0, 1.5, 2.0, 2.5, 3.0];
    let p_blowout_window:  Vec<usize> = vec![5, 10, 15, 20];

    let mut strat_id: usize = 0;
    let t_start = Instant::now();

    // Helper that materializes one strategy: directions over the full funding
    // grid, then splits per-window OOS PnL and writes a CSV.
    let emit_strategy = |
        strat_id: &mut usize, model: &str, p1: &str, p2: &str,
        directions: &[i32],
        out_root: &PathBuf, index: &mut fs::File,
        windows: &[(usize, usize, usize, usize)],
        probe_times: &[i64], event_rates: &[f64], pnl_at: &dyn Fn(i32, usize) -> f64,
        elapsed: f64,
    | {
        let out_path = out_root.join(format!("t6_{:05}.csv", strat_id));
        let mut f = fs::File::create(&out_path).unwrap();
        writeln!(f, "i,t_s,wfo_window,direction,rate,pnl_inc,equity").unwrap();
        let mut oos_pnls = [0.0f64; 3];
        let mut equity = 0.0f64;
        let mut n_signals = 0usize;
        for i in 0..directions.len() {
            let mut win_id: u8 = 0;
            for (wi, (_, _, os, oe)) in windows.iter().enumerate() {
                if i >= *os && i < *oe { win_id = (wi + 1) as u8; break; }
            }
            // PnL at event i uses direction at i-1 (held into event i).
            let dir = if i == 0 { 0 } else { directions[i - 1] };
            let inc = if win_id > 0 { pnl_at(dir, i) } else { 0.0 };
            if win_id > 0 {
                oos_pnls[(win_id - 1) as usize] += inc;
                equity += inc;
                if dir != 0 { n_signals += 1; }
            }
            writeln!(f, "{},{},{},{},{:.8},{:.8},{:.8}",
                i, probe_times[i], win_id, directions[i],
                event_rates[i], inc, equity
            ).unwrap();
        }
        let total = oos_pnls.iter().sum::<f64>();
        writeln!(index, "{:05},{},{},{},{},{:.6},{:.6},{:.6},{:.6},{},{:.4}",
            strat_id, model, p1, p2, out_path.display(),
            oos_pnls[0], oos_pnls[1], oos_pnls[2], total, n_signals, elapsed
        ).unwrap();
        *strat_id += 1;
    };

    // 1. PersistentFundingSignModel
    for &ms in &p_persistent {
        let m = PersistentFundingSignModel::new(ms).expect("persistent");
        let t0 = Instant::now();
        let dirs: Vec<i32> = probe_times.iter().map(|&t| {
            m.signal_at(&funding, t).direction as i32
        }).collect();
        let el = t0.elapsed().as_secs_f64();
        emit_strategy(&mut strat_id, model_name(ModelKind::Persistent),
                      &ms.to_string(), "", &dirs,
                      &out_root, &mut index, &windows, &probe_times,
                      &event_rates, &pnl_at, el);
    }

    // 2. FundingMomentumModel
    for &w in &p_momentum_window {
        for &k in &p_momentum_k {
            let m = FundingMomentumModel::new(w, k).expect("momentum");
            let t0 = Instant::now();
            let dirs: Vec<i32> = probe_times.iter().map(|&t| {
                m.signal_at(&funding, t).direction as i32
            }).collect();
            let el = t0.elapsed().as_secs_f64();
            emit_strategy(&mut strat_id, model_name(ModelKind::Momentum),
                          &w.to_string(), &format!("{:.2}", k), &dirs,
                          &out_root, &mut index, &windows, &probe_times,
                          &event_rates, &pnl_at, el);
        }
    }

    // 3. FundingOICointegrationModel
    for &w in &p_coint_window {
        for &k in &p_coint_k {
            let m = FundingOICointegrationModel::new(w, k).expect("oi_coint");
            let t0 = Instant::now();
            let dirs: Vec<i32> = probe_times.iter().map(|&t| {
                m.signal_at(&funding, &oi, t).direction as i32
            }).collect();
            let el = t0.elapsed().as_secs_f64();
            emit_strategy(&mut strat_id, model_name(ModelKind::OICoint),
                          &w.to_string(), &format!("{:.2}", k), &dirs,
                          &out_root, &mut index, &windows, &probe_times,
                          &event_rates, &pnl_at, el);
        }
    }

    // 4. BasisBlowoutTrigger as a standalone basis-side strategy: when a blowout
    //    fires, take direction = trigger.direction for the next funding event.
    for &k in &p_blowout_k {
        for &w in &p_blowout_window {
            let trig = BasisBlowoutTrigger::new(w, k).expect("blowout");
            let t0 = Instant::now();
            let evs = trig.run(&basis);
            let dirs: Vec<i32> = probe_times.iter().map(|&t| {
                let recent = evs.iter().rev().find(|e| e.time_s <= t && t - e.time_s < 86400 * 2);
                recent.map(|e| e.direction as i32).unwrap_or(0)
            }).collect();
            let el = t0.elapsed().as_secs_f64();
            emit_strategy(&mut strat_id, model_name(ModelKind::BasisBlowout),
                          &w.to_string(), &format!("{:.2}", k), &dirs,
                          &out_root, &mut index, &windows, &probe_times,
                          &event_rates, &pnl_at, el);
        }
    }

    eprintln!("DONE T6 — {} strategies in {:.2}s", strat_id, t_start.elapsed().as_secs_f64());
}
