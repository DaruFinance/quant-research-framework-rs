# Cross-engine parity status

Every surface below is either **green** (both engines agree within tolerance,
gated in CI) or a **known divergence** (labeled, with the reason and why it is
not a CI gate). There are no silent gaps. Published tolerance is `1e-3`
relative; the maximum observed deviation on the core surfaces is below `5e-5`
(the ledger's `%.4f` print-precision floor).

Reproduce all green surfaces at once:

```bash
make parity     # or: python tools/parity_check.py && parity_regime.py && parity_forex.py ...
```

## Green: gated in CI (`.github/workflows/parity.yml`)

| Surface | Harness | Result |
|---|---|---|
| Default config (SOL 1h) | `parity_check.py` | 56/56 metric points @ 1e-3 |
| Default config (BTC 30m, 146k bars) | `parity_check.py` | agree @ 1e-3 |
| Default config (DOGE 30m, 117k bars) | `parity_check.py` | agree @ 1e-3 |
| Default config (100k synthetic GBM) | `parity_check.py` | agree @ 1e-3 |
| Regime + WFO (SOL 1h) | `parity_regime.py` | 98/98 metric points @ 1e-3 |
| Forex mode (EURUSD 1h) | `parity_forex.py` | 56/56 metric points @ 1e-3 |
| Ledger, row-by-row (SOL 1h) | `parity_ledger.py` | per-trade agree @ 1e-3 |
| Shared indicators (SMA/EMA/MACD/RSI/ATR/Stochastic) | `parity_indicators.py` | agree @ 1e-3 |
| Volume indicators + strategy | `parity_volume.py` | agree @ 1e-3 |
| IS objective surface (2-axis, SL-3axis, bar-Sharpe) | `parity_surface.py` | agree @ 1e-3 |
| PBO / CSCV | `parity_pbo.py` | agree @ 1e-3 |
| Deflated multiple-testing haircuts | `parity_multitest.py` | agree @ 1e-3 |
| Deflated Sharpe (DSR) | `parity_dsr.py` | agree @ 1e-3 |
| Overfit-report safety (no line carries a metric body) | `parity_overfit_lines.py` | guard passes |
| Panel substrate (regime / baskets / ERC / neutralisations) | `parity_panel.py` | agree (ERC iterative solver ~3e-5) |
| Pairs primitives (spread / screener / cadence / stops) | `parity_pairs.py` | agree @ 1e-3 |
| Carry primitives (funding / basis / OI / triggers / models) | `parity_carry.py` | agree @ 1e-3 |
| Frozen robustness benchmark (18 core cells) | `tools/benchmark.py --cross-engine` | 144 comparisons, 0 bad @ 1e-3 |

The v0.5.0 multi-asset substrate (panel / pairs / carry) **is** re-verified at
v0.6.0: those three harnesses run in CI on every push, so the substrate is
green, not a deferred gap.

## Known divergences: labeled, not gated

1. **Four-way combo** (`parity_combo.py`: regime + WFO + forex + session, all
   at once). CLOSED. Passes 70/70 metric points on EURUSD 1h and SOLUSDT 1h.
   Two causes were found: a harness mismatch driving the engines with different
   selection knobs, and three session-end-bar divergences in the Rust core
   (an opposite-flip entry could override the force-close, a new entry was not
   blocked on the closing bar, and the intrabar SL/TP check still ran). All
   three are now guarded by the session flag, leaving the single-feature
   surfaces byte-unchanged. Gated in CI via `tools/sweep_all.sh`.

2. **USD/JPY in the frozen benchmark.** CLOSED at v0.7.4. The dataset is no
   longer excluded: all 18 core cells gate green, 144 comparisons, 0 bad @ 1e-3.

   The reason previously recorded here was wrong. It blamed a JPY `pip_size`
   path `parity_forex` did not cover, but that harness gates USD/JPY green at
   1e-3 and has since v0.7.0. The actual cause was a third, unguarded copy of
   the EMA recursion, private to `examples/benchmark_runner.rs`, which missed
   the pandas constant-run guard that divergence 3 below describes. On USD/JPY's
   forward-filled flat bars it drifted one ulp off the reference, so the strict
   comparisons in `signal_ema_cross` resolved to +/-1 where the reference gives
   0: that cell harvested 476 OOS trades against the reference's 474 (~1.3% on
   the net metrics), and `signal_macd_zero` drifted ~0.15% on an otherwise
   identical 2,036-trade stream. `eff_oos_bars` agreed on all 15 non-USD/JPY
   cells throughout, which is what localised it.

3. **USD/JPY per-trade ledger, one trade of 1,727** (`parity_ledger.py --forex`
   on `USDJPY_1h`). CLOSED at v0.7.4. Passes 1,727/1,727 trades and all 8,635
   compared fields, gated in CI like every other ledger surface.

   The cause was a floating-point tie, not a segment boundary. Both engines
   resolved the same W03 OOS slice (`bars[7648:12648]`), both loaders read all
   87,648 rows, and the SL/TP touch comparisons were operator-for-operator
   identical. The slice opens on a run of constant 112.551 closes, on which
   the fast and slow EMAs are equal in exact arithmetic. The two ports reached
   that equality differently. Both run the same recursion,
   e[i] = a*x[i] + (1-a)*e[i-1], but pandas guards it: the ewm kernel skips
   the update when the running average already equals the incoming
   observation, so on a constant run seeded at that value it returns the seed
   and a gap of exactly 0.0. This port applied the multiply-and-add
   unconditionally and returned -1.42e-14.

   That is one unit in the last place at this price level, and it was enough to
   make the strict `fast < slow` test fire here and not in the reference. The
   residue is not specific to this series: across 780 probed (span, price)
   pairs the unguarded form lands one ulp off the guarded one in 125 of them.

   It surfaced on this dataset and no other because 25,325 of USD/JPY's 87,648
   bars (28.9%) have zero range: weekend and gap-fill bars where O = H = L = C.
   A flat run is both where the recursions can differ by an ulp and where every
   entry stops out on the next bar for a full -1R, so one extra signal became
   one extra complete trade.

   The fix mirrors the pandas guard: skip the update when the running average
   already equals the incoming observation. It is applied at every site in this
   crate that reimplements a pandas `ewm`, not only the one the bug surfaced
   through: `compute_ema` in `src/lib.rs`, `compute_ema` and the NaN-tolerant
   `ema` in `src/indicators.rs`, `src/volume.rs`, `src/panel/regime.rs`, and
   the private copies in `examples/benchmark_runner.rs`, `examples/batch_runner.rs`
   and `examples/atr_cross.rs`. `ewm_adjusted` is left alone: the `adjust=True`
   accumulator is a different algorithm and does not carry the same trap.
   `tests/invariants.rs` now asserts a constant run stays exactly flat, so the
   bug cannot come back unnoticed.

4. **Monte Carlo percentiles**: *intentional.* Python draws from NumPy's
   global RNG; Rust uses `StdRng` seeded to 42. Different algorithms, so the
   percentile values differ while the distribution shape matches. Disable Monte
   Carlo and this surface is identical.

5. **`INDICATOR_VARIANCE` robustness overlay**: *seeded, and gated.* Both
   engines pick the ±1 lookback shift from an RNG seeded to 42
   (`IND_VARIANCE_SEED` in Rust, `random.Random(42)` in Python), so the
   `W*_IS+ENT+IND` / `W*_OOS+ENT+IND` lines are reproducible run-to-run and
   are covered by the byte-identical cross-architecture golden check.

6. **Robustness scenario sets differ between the ports.** CLOSED at v0.7.4.
   Rust's `robustness_scenarios()` defined five scenarios against the
   reference's four, the extra one being the news-candle injection, which the
   reference drops by design. Rust therefore printed `NEWS IS` / `NEWS OOS1`
   stages that Python never emits, which was the 194-vs-196 tagged-line gap.
   The port now runs the same four scenarios and both engines print 194 tagged
   metric lines. `inject_news_candles` stays in the crate, unreferenced by the
   default scenario set, exactly as the reference keeps its own
   `inject_news_candles` outside `ROBUSTNESS_SCENARIOS`.

7. **Printed stage labels differed.** CLOSED at v0.7.5. The port annotated its
   `Baseline` and robustness stages with the selected lookback (`Baseline IS
   (LB 47)`) where the reference prints them bare. Not a numerical divergence,
   and no compared value moved: the regenerated goldens are value-identical
   once the `(LB nn)` prefix is stripped. It mattered because a label that
   differs cannot be compared tag-to-tag, so those lines sat outside the diff's
   reach even in principle. Both engines now emit the same 194 tags, and the
   parity whitelist is a scope decision alone.

## Python-only: no Rust counterpart, not cross-engine-checked by design

These ship in the Python reference and have no Rust port, so there is nothing
to diff. They are research/example surface, not the parity-gated core engine:

- **`backtester/bootstrap.py`**: stationary bootstrap (Politis & Romano 1994)
  for serial-correlated resampling / variance estimation. Python-only utility.
- **`examples/ml_*`**: ML example strategies (`ml_sklearn`, `ml_regime_kmeans`,
  `ml_callback`, `ml_precomputed`). Illustrate the `create_raw_signals` contract
  with scikit-learn; they are examples, not engine surfaces.

If any of these gains a Rust implementation, it moves to the **green** table
with its own `parity_*` harness and CI step.
