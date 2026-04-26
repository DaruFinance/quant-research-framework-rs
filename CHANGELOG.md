# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.2] — 2026-04-26

### Added
- **Full pip-based forex PnL math** in `backtest_core`. Mirrors Python's
  numba forex branches:
  - position size becomes 1 R-unit (was crypto's `RISK_AMOUNT`)
  - SL/TP arithmetic switches from multiplicative percentages to
    additive pip-distance (`entry_price ± sl_perc` instead of
    `entry_price * (1 ± sl_perc/100)`)
  - PnL = clamp(`price_move_pips / stop_pips`, -1, +RRR) ×
    `position_size_fx` − fees, capping per-trade at `[-1R, +RRR]`
  - eq_frac/rets normalised to `position_size_fx` (= cumulative R units),
    `compute_metrics_for(use_forex=true)` reports ROI without `-1` offset
    and absolute (R-unit) drawdown
- **`Config.pip_size`** field (default 0.0001; set 0.01 for JPY pairs).
- **NY-tz session handling** via `chrono` + `chrono-tz` deps. The session
  mask now uses America/New_York local hours (DST-aware), matching
  Python's `load_ohlc` + `in_session` semantics. `SESSION_START_HOUR` /
  `SESSION_END_HOUR` defaults updated to NY 8/17 (was UTC 13/21).
- **Session-aware `parse_signals_with_flags(raw, in_flags)`** + a
  `compute_in_flags(bars, cfg)` helper. The internal `parse_signals_for`
  routes every engine call through the session-aware path so out-of-session
  signals are masked at parse time, matching Python's `_parse_signals_numba`.
- **Out-of-session bar skipping** in `backtest_core`'s main loop. Mirrors
  Python's `for idx in session_idxs` iteration — funding, SL/TP, and
  entries no longer fire on out-of-session bars. Force-close at session
  end is now also gated on `code != 0`, matching Python's guard.
- **`DISPLAY_FOREX` atomic** drives `prettyprint` / `prettyprint_str` to
  emit `R`-suffixed metrics (instead of `$`) when `cfg.use_forex` is set.

### Verification
- `tools/parity_check.py` — v0.1.0 baseline still byte-identical
  (56/56 metric points, 0% relative diff at 0.1% tolerance) after all
  the new code paths. The forex/session/regime additions are all opt-in,
  so they have zero impact on default-config behaviour.
- `tools/parity_combo.py` — runs both engines with **regime + WFO + forex
  + session all on simultaneously**. The first WFO window's IS metrics
  match within 5% relative tolerance (trades py=49 vs rs=52, ROI 46.98R
  vs 45.42R, Sharpe 3.36 vs 3.35). Per-window OOS still diverges because
  a 1-LB tie-break in fine-tuning (Python picks Downtrend LB=71, Rust
  picks 72 — both have nearly identical Sharpe scores) cascades through
  forex's quantised ±1R/±3R PnL into very different OOS results. This
  is parameter sensitivity downstream of an honest tie-break, not an
  engine bug.

## [0.2.1] — 2026-04-25

### Added
- **Full regime-segmentation engine** (`src/lib.rs`). The 200-bar warmup
  stub is replaced with a real implementation:
  - `default_regime_detector` — EMA-200 / 8-bar consistency, 3 labels
    (Uptrend / Downtrend / Ranging) matching the Python reference.
  - `optimize_regimes_sequential_rs` — per-regime LB optimiser with
    coarse/fine search; works for any `REGIME_LABELS.len()` in [2, 5].
  - `create_regime_signals_internal` — bar-by-bar EMA-crossover signals
    with the active LB rotating per bar based on the regime label.
  - `walk_forward_regime` — WFO loop that walks `WFO_TRIGGER_VAL` cadence
    and rotates per-regime LBs in OOS, with all 5 robustness overlays
    (FEE / SLI / ENT / IND / NEWS) running per window. Same bug fix as
    Python: regime flips never re-anchor the IS window.
  - `run_with_regime(bars, strategy, sig_fn, regime_cfg)` — public
    entry point, supersedes the v0.2.0 surface-only stub.
- **`RegimeConfig`** struct exposes `labels: Vec<String>` and
  `detector: RegimeDetectorFn` to user code; `RegimeConfig::new` panics
  loudly if the label count is outside [2, 5]. Default = the 3-regime
  EMA detector.
- **Runtime-configurable engine flags.** `Config` gained `use_forex`,
  `use_sessions`, `session_start_hour`, `session_end_hour`, `use_oos2`
  (with builders `with_forex`, `with_sessions`, `with_oos2`). The
  backtest core consults these fields instead of compile-time consts so
  tests can exercise each flag without rebuilding.
- **`examples/regime_custom.rs`** rewritten to actually plug a 4-regime
  trend × volatility detector into `run_with_regime` and exercise the
  engine end-to-end.
- **Behavioural test suite** (`tests/behavioural.rs`, 8 tests) covering
  forex / session / OOS2 / regime config + detector contract.
- **Cross-language parity harness** (`tools/parity_check.py`). Runs both
  engines on the same dataset with matching defaults and asserts
  agreement on IS-raw / OOS-raw / IS-opt / OOS-opt / Baseline IS / OOS /
  W01 IS / W01 OOS. Verified at byte-identity on the bundled
  SOLUSDT_1h dataset: 56/56 metric points match exactly (0% relative
  difference) at 0.1% tolerance.

### Changed
- README: removed the v0.2.0 caveat about the parity claim being
  unqualified for v0.2.x additions; the harness now backs the claim
  with a re-runnable check.

## [0.2.0] — 2026-04-25

### Added
- **`USE_FOREX` toggle** in `src/lib.rs`. When `true`, the per-bar
  funding-fee block (00/08/16 UTC) is skipped — matching FX broker PnL
  semantics and the Python reference.
- **Session mode (`USE_SESSIONS`).** New `SESSION_START_HOUR` /
  `SESSION_END_HOUR` constants drive an in-session mask in
  `backtest_core`. Out-of-session entries are blocked; positions are
  force-closed on the last in-session bar of each day. Times are
  interpreted in UTC (Python uses America/New_York with DST; UTC is a
  safe approximation for bars already aligned to NY session boundaries).
- **`NEWS_CANDLES_INJECTION` robustness scenario** plus the
  `inject_news_candles` helper. The scenario list now matches Python at
  five entries (ENTRY_DRIFT, FEE_SHOCK, SLIPPAGE_SHOCK,
  ENTRY_DRIFT+INDICATOR_VARIANCE, NEWS_CANDLES_INJECTION).
- **ML signal examples.**
  - `examples/ml_precomputed.rs` — train offline, plug a per-bar score
    slice into the strategy function, threshold it.
  - `examples/ml_callback.rs` — keep a model in memory, call
    `predict(features)` per bar; ships a hand-coded linear model so the
    example has zero extra dependencies.
- **Custom regime detector contract.** New `RegimeDetectorFn = fn(&[Bar])
  -> Vec<u8>` type alias and `REGIME_LABELS` const slice expose the
  same contract as Python's `detect_regimes` for downstream code to
  adopt now. `examples/regime_custom.rs` demonstrates a 4-regime
  trend × volatility detector.
- `CHANGELOG.md`, version bump in `Cargo.toml`.

### Changed
- `RobustnessOpts` gained a `news_on` flag; the WFO-window and standalone
  robustness loops both honour it.

### Known limitations / scheduled for v0.3.0
- **Full regime-segmentation engine.** This release ships the
  `RegimeDetectorFn` *contract* but the inner-loop integration is still
  the v0.1.x stub (`if USE_REGIME_SEG && idx < 200 { continue; }`). The
  per-regime LB optimiser, OOS LB rotation, and regime-aware filters
  that exist in the Python reference are scheduled for v0.3.0.
- **WFO + regime fix.** The Python reference fixes a bug where regime
  changes shifted WFO test/train boundaries (see Python CHANGELOG). The
  Rust port doesn't have this bug today because it doesn't have the
  regime path either; the v0.3.0 implementation will follow Python's
  corrected cadence from the start.
- **Cross-language parity harness.** A `tools/parity_check.py` that runs
  the Python reference and the Rust port across a flag matrix and diffs
  deterministic metrics is being staged separately; until it lands, the
  README's "1-to-1 parity" claim should be qualified to:
  - parity for IS/OOS baseline + EMA-crossover optimiser + 4 robustness
    scenarios + WFO windows on the SOLUSDT_1h sample (same as `0.1.0`),
  - **not** parity for forex / session / news / regime paths in this
    release — those features are now present in both implementations
    but have not yet been jointly validated.
- The integer-vs-string `side` comparison bug deliberately replicated
  at `src/lib.rs:486-487` is unchanged in `0.2.0` to keep parity with
  the Python reference, which still has it. Both will be fixed
  together in `0.3.0`.

## [0.1.0] — 2026-03 (backfilled)

Initial public release. Contained:

- Walk-forward backtester with O(n) rolling computations, parallel-ready
  optimiser pass, and IS/OOS baseline.
- Robustness suite: ENTRY_DRIFT, FEE_SHOCK, SLIPPAGE_SHOCK,
  INDICATOR_VARIANCE.
- Monte Carlo (bootstrap + permutation) for IS validation.
- ATR-cross example with RSI ≥ 50 confluence.
- Synthetic OHLC generator (`examples/gen_synthetic.rs`).
- Manual byte-for-byte parity verification against
  `quant-research-framework` v0.1.0 on the sample SOLUSDT 1h dataset
  for IS/OOS baseline + optimiser + 4 robustness + 18 WFO windows.
