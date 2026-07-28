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

3. **USD/JPY per-trade ledger, one trade of 1,727** (`parity_ledger.py --forex`
   on `USDJPY_1h`). Rust opens one short that Python does not, at
   `entry_unix = 1486317600`. Every one of the 1,727 shared trades agrees on
   all 8,635 compared fields.

   The cause is a floating-point tie, not a segment boundary. Both engines
   resolve the same W03 OOS slice (`bars[7648:12648]`), both loaders read all
   87,648 rows, and the SL/TP touch comparisons are operator-for-operator
   identical. The slice opens on a run of constant 112.551 closes, on which
   the fast and slow EMAs are equal in exact arithmetic. The two ports reach
   that equality by algebraically identical but numerically different
   recursions:

       pandas ewm(adjust=False):  e[i] = e[i-1] + a*(x[i] - e[i-1])  ->  gap 0.0
       this port:                 e[i] = a*x[i] + (1-a)*e[i-1]       ->  gap -1.42e-14

   That is one unit in the last place at this price level, and it is enough to
   make the strict `fast < slow` test fire here and not in the reference.

   It surfaces on this dataset and no other because 25,325 of USD/JPY's 87,648
   bars (28.9%) have zero range: weekend and gap-fill bars where O = H = L = C.
   A flat run is both where the recursions can differ by an ULP and where every
   entry stops out on the next bar for a full -1R, so one extra signal becomes
   one extra complete trade.

   `tools/sweep_all.sh` runs this check through `run_known`, which **still runs
   the comparison and still prints the diff**. It pins the count at exactly 1
   at that trade key: any other number or key fails the sweep.

4. **Monte Carlo percentiles**: *intentional.* Python draws from NumPy's
   global RNG; Rust uses `StdRng` seeded to 42. Different algorithms, so the
   percentile values differ while the distribution shape matches. Disable Monte
   Carlo and this surface is identical.

5. **`INDICATOR_VARIANCE` robustness overlay**: *seeded, and gated.* Both
   engines pick the ±1 lookback shift from an RNG seeded to 42
   (`IND_VARIANCE_SEED` in Rust, `random.Random(42)` in Python), so the
   `W*_IS+ENT+IND` / `W*_OOS+ENT+IND` lines are reproducible run-to-run and
   are covered by the byte-identical cross-architecture golden check.

6. **Robustness scenario sets differ between the ports.** Rust's
   `robustness_scenarios()` defines five scenarios; the Python reference
   defines four and omits the news-candle injection. Rust therefore prints
   `NEWS IS` / `NEWS OOS1` stages that Python never emits, which is the
   194-vs-196 tagged-line gap. The parity gate does not see this: those tags
   are outside the compared whitelist. This is a real semantic divergence,
   not a formatting artefact.

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
