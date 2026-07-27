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
| Frozen robustness benchmark (15 core cells) | `tools/benchmark.py --cross-engine` | 120 comparisons, 0 bad @ 1e-3 |

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

2. **USD/JPY in the frozen benchmark** (`cross_engine_check = false` in
   `tools/benchmark_manifest.toml`). The JPY `pip_size = 0.01` forex path has a
   ~1–2% Python↔Rust residual that `parity_forex` (EUR/USD, `pip_size = 0.0001`)
   does not exercise. The NET/GROSS numbers for USD/JPY are still reported in
   the golden; only the cross-engine **gate** skips that one dataset, with the
   reason inline in the manifest. The other 15 core cells gate green.

3. **Monte Carlo percentiles**: *intentional.* Python draws from NumPy's
   global RNG; Rust uses `StdRng` seeded to 42. Different algorithms, so the
   percentile values differ while the distribution shape matches. Disable Monte
   Carlo and this surface is identical.

4. **`INDICATOR_VARIANCE` robustness overlay**: *intentional.* Both engines
   pick the ±1 lookback shift from an unseeded RNG, so the
   `W*_IS+ENT+IND` / `W*_OOS+ENT+IND` lines jitter run-to-run in both. This is
   a property of the reference, reproduced faithfully.

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
