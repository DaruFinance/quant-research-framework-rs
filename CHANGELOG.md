# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] — 2026-04-30

### Added
- **Forex-mode parity (third passing surface).** The new
  `tools/parity_forex.py` harness runs both engines with
  `FOREX_MODE=true` on a bundled EURUSD 1h CSV (`data/EURUSD_1h.csv`,
  53,160 bars). At v0.3.0 it reported 52 / 56 mismatches; the three
  closures below land it at 0 / 56 mismatches with max relative
  deviation below the metric ledger's printed precision floor.
- **`Config::with_forex_defaults()`** builder method:
  `use_forex=true`, `position_size=1.0`, `account_size=1.0`. Mirrors
  Python's module-level `RISK_AMOUNT=ACCOUNT_SIZE=POSITION_SIZE=1.0`
  setup at `FOREX_MODE=True`.
- **`Config::account_size`** field (default 100,000), replacing
  hard-coded use of the crate constant inside the equity-list seed
  and metric normalisation. Lifts the sole remaining pip-mode
  scaling constant out of the engine and into the configuration
  surface.
- **`Config::mask_exits`** field (default `false`) for API symmetry
  with Python's new `MASK_EXITS` flag. The Rust crate does not yet
  implement the confluence machinery; the field is reserved.
- **`Config::legacy_side_bug`** field (default `false`) gating the
  pre-v0.3.1 RRR-side bug. When `true`, both the classic and
  regime-rotated optimiser code paths replicate the int-vs-str
  comparison that always took the short branch. Default is the
  corrected `t.side == 1` test.

### Fixed
- **RRR-probe pip scaling.** The optimiser's `risk = ep * SL_PERCENTAGE
  / 100.0` denominator used the un-scaled `SL_PERCENTAGE` constant
  (1.0). Python's same formula uses the global, which in `FOREX_MODE`
  has been pre-multiplied by `PIP_SIZE` (10⁻⁴). This 10,000×
  difference made every per-trade `peak_R` cap differently across
  engines, which made the probe pick different optimal RRRs (Python
  often 3, Rust often 1) and produce different SL/TP levels for every
  subsequent backtest. Now scales by `cfg.pip_size` when `use_forex`
  in both the classic and regime-rotated optimisers.
- **Intrabar SL/TP forex pnl.** For SL/TP-hit trades in forex mode,
  Python hard-codes the result to `-1.0` R (SL) or `tp_perc/sl_perc`
  R (TP) regardless of slippage; Rust ran the slippage-adjusted exit
  price through the general `trade_res` formula. The two are
  numerically identical to within a few ULPs per trade, but
  accumulated across hundreds of trades produced a ~4% ROI drift.
  Rust now mirrors Python's hard-coded R-unit branch.

## [0.3.0] — 2026-04-26

### Added
- **Regime+WFO byte-identical parity** with the Python reference. The
  regime-segmentation code path was shipped as a contract in v0.2.0 and
  partially implemented from v0.2.1, but produced metrics that differed
  from Python on every WFO window. v0.3.0 closes the gap. Verified by
  the new `tools/parity_regime.py` harness on the bundled `SOLUSDT_1h.csv`
  at 0.001 relative tolerance: every `IS-raw`, `OOS-raw`, `IS-opt`,
  `OOS-opt`, `Baseline IS/OOS`, and `W01..W04 IS/OOS` line matches
  byte-for-byte in trade count, ROI, PF, Sharpe, win rate, expectancy
  and max drawdown.
- **`tools/bench.py`** — cross-language benchmark harness. Times both
  engines across multiple dataset sizes with `/usr/bin/time -v`, emits a
  reproducible markdown table for the README. Replaces the unsourced
  "~24× faster" claim with measured numbers.
- **`tools/parity_regime.py`** — dedicated regime+WFO parity check (sets
  `USE_REGIME_SEG=True, USE_WFO=True` and leaves all other flags at
  default). Asserts byte-identity at the chosen tolerance and returns
  exit 1 on any divergence.
- **Comparison matrix** in `README.md` — this framework vs vectorbt /
  backtrader / NautilusTrader / zipline-reloaded / Lean / bt across
  built-in WFO, per-regime LB optimisation, strict-LAH property tests,
  cross-language byte-parity. Verified against primary docs as of
  2026-04.
- **`CITATION.cff`** with sibling cross-reference to the Python repo so
  citing either implies citing the framework as a whole.
- `Config::use_regime_seg` field — gates the 200-bar warm-up in the
  backtest core, mirroring Python's global `USE_REGIME_SEG` flag.
  `run_with_regime` and `run_with_regime_cfg` flip this on
  automatically; default `Config::new()` keeps it off (zero-cost for
  classic-path users).
- `tests/contract_v0_2.rs::config_use_regime_seg_defaults_off_and_is_settable`
  pinning the new field's contract. 23 Rust tests total now passing.

### Fixed
- Three regime-path bugs that drove the v0.2.x parity gap, all now
  byte-identical with the Python reference:
  1. **`optimize_regimes_sequential_rs` did not probe RRR per regime.**
     Each LB candidate is now scored at its regime-best RRR (probe
     TP=5×SL → restrict R-collection to in-regime trades → pick
     `RRR ∈ {1..5}` maximising sum-of-R → re-run at chosen RRR), exactly
     mirroring Python's `optimize_regimes_sequential::_evaluate`.
     Returns `(best_lbs, best_rrrs)` instead of just `best_lbs`.
  2. **LB candidate list now excludes `FAST_EMA_SPAN`** — Python's
     regime-path optimiser uses
     `[lb for lb in range(*LOOKBACK_RANGE) if lb != FAST_EMA_SPAN]`
     while the classic optimiser keeps it. Rust used the classic list
     for both, shifting the coarse-pass step-by-2 indices and picking
     different LBs per regime.
  3. **RRR probe used raw `close[idx]` instead of the trade's
     slippage-adjusted entry/exit price.** Python's regime probe reads
     `entry` and `exit_p` from the trade tuple (which include slippage);
     Rust now reads `t.entry_price` / `t.exit_price` to match. The
     classic-path optimiser still uses `close[idx]` because Python's
     classic optimiser does too — they're separate code paths.
- **`optimize_regimes_sequential_rs` re-detects regimes locally on the
  IS slice**, mirroring Python's `optimize_regimes_sequential` (which
  computes EMA_200 on the local copy, then `detect_regimes(dfi)`). The
  actual IS/OOS run still uses globally-detected regimes sliced — same
  two-regime-source design Python uses.
- Removed the orphaned `if USE_REGIME_SEG && idx < 200 { continue; }`
  warm-up stub. The 200-bar warm-up now lives at the same position in
  the inner loop but is gated by `cfg.use_regime_seg` instead of a dead
  compile-time const, so it can actually fire.

### Notes
- `tools/parity_check.py --tol 0.001` still reports **56/56**
  byte-identical metric points against the Python reference (the
  default-config surface is unchanged).
- `tools/parity_combo.py` (regime + WFO + forex + session +
  `OPTIMIZE_RRR=False` + `MIN_TRADES=1`, all on at once) still reports
  diffs in trade counts even on the classic baseline (`Baseline IS`,
  `Baseline OOS`). The remaining gap is in the forex/session
  *interaction*, not in the regime engine itself; combinations that
  layer all four v0.2.x features are not part of v0.3.0's parity
  guarantee. Single-feature parity (default, regime+WFO) is.

## [0.2.4] — 2026-04-26

### Fixed
- **Session-end force-close fires unconditionally** when an open
  position exists and the bar is the last in-session bar of the day.
  v0.2.2/0.2.3 mirrored Python's `code != 0` guard for parity, which
  caused positions to carry across out-of-session gaps when no signal
  landed on the closing bar. Python v0.2.3 also dropped the guard, so
  both engines now agree on the corrected behaviour.

### Added
- `tests/invariants.rs::session_end_marks_last_in_session_bar_per_day`
  — verifies the session_end mask marks exactly one bar per NY day
  (the last in-session bar before the day rolls out of session). 22
  Rust tests total now passing.

### Notes
- These changes only affect `cfg.use_sessions = true` runs. Default-config
  parity preserved: `tools/parity_check.py --tol 0.001` still reports
  56/56 byte-identical metric points against the Python reference.

## [0.2.3] — 2026-04-26

### Added
- **Property-check suite** (`tests/invariants.rs`, 10 tests). Verifies
  the engine respects the contracts it claims rather than just "does
  the flag change anything":
  - parse_signals emits no flip codes on out-of-session bars (when
    in_flags supplied)
  - compute_in_flags uses NY local hours (DST-aware) and matches what
    backtest_core consumes; returns all-true when sessions off
  - Forex `Config` builder switches to pip semantics + JPY override
    round-trips
  - Default regime detector emits only labels in [0, 2] for every bar
  - Default detector is look-ahead-clean (mutating bars[cut..] doesn't
    change labels[..cut])
  - RegimeConfig accepts each label count 2..=5
  - parse_signals length-preserves and emits only valid {0,1,2,3,4} codes
  - parse_signals is look-ahead-clean
  - compute_ema matches recursive `alpha = 2/(span+1)` form

### Verified
- v0.1.0 parity still 56/56 byte-identical at 0.1% tol.
- 21 Rust tests total (8 behavioural + 3 contract + 10 invariants).

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
