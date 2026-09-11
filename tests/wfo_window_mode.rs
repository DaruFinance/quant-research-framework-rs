use quant_research_framework_rs::{
    default_ema_signal, load_ohlc, walk_forward_collect, Config, WfoWindowMode,
};

fn config(mode: WfoWindowMode, suffix: &str) -> Config {
    let mut cfg = Config::new().with_wfo_window_mode(mode);
    cfg.oos_candles = 12_500;
    cfg.use_monte_carlo = false;
    cfg.export_path = std::env::temp_dir()
        .join(format!("qrf-rust-test-{suffix}-{}.csv", std::process::id()))
        .to_string_lossy()
        .into_owned();
    cfg
}

#[test]
fn real_bar_expanding_geometry_and_causality() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/data/SOLUSDT_1h.csv");
    let source = load_ohlc(path);
    let bars = &source[..24_000];

    let mut rolling_cfg = config(WfoWindowMode::Rolling, "rolling");
    let rolling = walk_forward_collect(
        bars,
        &[1.0],
        &mut rolling_cfg,
        "test",
        default_ema_signal,
    );
    let mut expanding_cfg = config(WfoWindowMode::Expanding, "expanding");
    let expanding = walk_forward_collect(
        bars,
        &[1.0],
        &mut expanding_cfg,
        "test",
        default_ema_signal,
    );

    assert_eq!(rolling.per_window_is_ranges, vec![(1_500, 11_500), (6_500, 16_500), (11_500, 21_500)]);
    assert_eq!(expanding.per_window_is_ranges, vec![(1_500, 11_500), (1_500, 16_500), (1_500, 21_500)]);
    assert_eq!(rolling.per_window_oos_ranges, expanding.per_window_oos_ranges);
    assert_eq!(expanding.per_window_oos_ranges, vec![(11_500, 16_500), (16_500, 21_500), (21_500, 24_000)]);
    assert_eq!(rolling.per_window_lbs[0], expanding.per_window_lbs[0]);
    assert_eq!(expanding.per_window_lbs.len(), 3);
    assert!(expanding.per_window_oos.iter().all(|metrics| metrics.trades > 0));

    let mut polluted = bars.to_vec();
    for bar in &mut polluted[16_500..] {
        bar.open *= 7.0;
        bar.high *= 7.0;
        bar.low *= 7.0;
        bar.close *= 7.0;
    }
    let mut polluted_cfg = config(WfoWindowMode::Expanding, "polluted");
    let rerun = walk_forward_collect(
        &polluted,
        &[1.0],
        &mut polluted_cfg,
        "test",
        default_ema_signal,
    );
    assert_eq!(expanding.per_window_lbs[0], rerun.per_window_lbs[0]);
    let a = &expanding.per_window_oos[0];
    let b = &rerun.per_window_oos[0];
    assert_eq!(a.trades, b.trades);
    assert_eq!(a.roi, b.roi);
    assert_eq!(a.pf, b.pf);
    assert_eq!(a.sharpe, b.sharpe);

    let mut current_oos_polluted = bars.to_vec();
    for bar in &mut current_oos_polluted[11_500..] {
        bar.open *= 11.0;
        bar.high *= 11.0;
        bar.low *= 11.0;
        bar.close *= 11.0;
    }
    let mut current_cfg = config(WfoWindowMode::Expanding, "current-oos");
    let current = walk_forward_collect(
        &current_oos_polluted,
        &[1.0],
        &mut current_cfg,
        "test",
        default_ema_signal,
    );
    assert_eq!(expanding.per_window_lbs[0], current.per_window_lbs[0]);
}

#[test]
#[should_panic(expected = "expanding WFO requires data length")]
fn expanding_rejects_insufficient_initial_history() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/data/SOLUSDT_1h.csv");
    let source = load_ohlc(path);
    let mut cfg = config(WfoWindowMode::Expanding, "short");
    cfg.oos_candles = 10_000;
    let _ = walk_forward_collect(
        &source[..19_999],
        &[1.0],
        &mut cfg,
        "test",
        default_ema_signal,
    );
}
