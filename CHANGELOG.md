# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
