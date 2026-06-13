# Quant Research Backtester — Rust port

[![parity](https://github.com/DaruFinance/quant-research-framework-rs/actions/workflows/parity.yml/badge.svg)](https://github.com/DaruFinance/quant-research-framework-rs/actions/workflows/parity.yml)
[![docs](https://github.com/DaruFinance/quant-research-framework-rs/actions/workflows/docs.yml/badge.svg)](https://github.com/DaruFinance/quant-research-framework-rs/actions/workflows/docs.yml)
[![crates.io](https://img.shields.io/crates/v/quant-research-framework-rs.svg)](https://crates.io/crates/quant-research-framework-rs)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.19798592.svg)](https://doi.org/10.5281/zenodo.19798592)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

**A walk-forward backtester written twice — a Python reference and a Rust port — that proves in CI the two produce the same numbers.** Walk-forward optimization (WFO), robustness stress tests, realism controls (fees, slippage, funding, SL/TP), and strict no-look-ahead enforced at the ledger level.

It answers one question: *does an apparent edge survive out-of-sample evaluation under realistic frictions, or is it just fitting the past?* The Rust port answers it **23.8–57× faster** and in **33–65× less memory** than the Python reference ([benchmarks](#performance)), and a parity harness checks both engines agree within `1e-3` on every push.

## Why two engines

Most backtesters ask you to trust one implementation. This one is written twice and diffed: the Python reference is the readable spec, the Rust port is the fast spec, and a parity oracle runs both on identical input and asserts the metrics match. If the port drifts from the reference, CI goes red — the correctness claim is *enforced, not asserted*.

```
                ┌────────────────────────────────────────────────┐
                │                same OHLC input                  │
                │     data/SOLUSDT_1h.csv · EURUSD_1h.csv · …      │
                └───────────────┬────────────────┬───────────────┘
                                │                │
                ┌───────────────▼──────┐  ┌──────▼───────────────┐
                │  Python reference    │  │      Rust port       │
                │  backtester/         │  │      src/lib.rs      │
                │  (the spec, readable)│  │   + metrics · dsr ·  │
                │                      │  │   pbo · opt_surface… │
                └───────────────┬──────┘  └──────┬───────────────┘
                                │ metrics        │ metrics
                                ▼                ▼
                ┌────────────────────────────────────────────────┐
                │              parity oracle  (CI)                │
                │      tools/parity_*.py  ·  assert |Δ| ≤ 1e-3    │
                │   default 56/56 · regime+WFO 98/98 · fx 56/56   │
                └────────────────────────┬───────────────────────┘
                                         │
                                 red if the port drifts
```

## Reproduce it in under 5 minutes

One command builds the Rust engine, runs the cross-language parity suite, and fires the no-look-ahead leak guard:

```bash
make repro     # set QRF_PY_DIR=/path/to/quant-research-framework if it isn't a sibling checkout
```

The parity surfaces compare both engines metric-by-metric and end in `PARITY OK`; the published counts are **56/56** (default config), **98/98** (regime + WFO), and **56/56** (forex). The leak demo plants a strategy that reads 4 bars into the future, NaN-pollutes the future, and shows that exactly those 4 *past* bars move while the shipped causal strategy does not:

```
look-ahead leak demo  —  n=800 bars, pollute future at cut=400
  causal EMA-cross (shipped)    :   0/400 past bars changed   PASS — no look-ahead
  forward-peek EMA-cross (H=4)  :   4/400 past bars changed   LEAK CAUGHT
```

Run any piece on its own with `make parity`, `make leak`, or `make bench`. The leak demo (`tools/leak_demo.py`) is the same pollute-and-verify property the Hypothesis suite runs over a generated input space (the Python reference's `tests/test_invariants_property.py`).

## What this is / what it isn't

**It is** a correctness-first WFO backtesting engine, in two languages, with the equivalence machine-checked: realism controls on by default (fees, slippage, funding, SL/TP with intrabar high/low checks), strict no-look-ahead enforced by ledger-level invariant tests, and overfitting diagnostics (DSR/PSR/MinTRL/MinBTL, PBO/CSCV, deflated multiple-testing haircuts).

**It isn't:**
- **Not alpha.** The bundled strategies (EMA-cross, ATR-cross, …) are plumbing to exercise the engine, not trade signals. There is no edge here to deploy.
- **Not live trading.** No broker connectivity, order management, or execution — it evaluates strategies on historical bars.
- **Tested on crypto, FX, and synthetic GBM only.** SOL/BTC/DOGE-USDT, EUR/USD, USD/JPY, and a GBM generator. Equities, futures, and options are untried.
- **Parity-gated on the core surfaces only.** The stationary-bootstrap module (`backtester/bootstrap.py`) and the `examples/ml_*` strategies are Python-only — no Rust counterpart, no cross-engine check. See [open parity gaps](#what-is-not-yet-jointly-validated).

## Quick Start

```bash
cargo build --release
./target/release/backtester                 # uses data/SOLUSDT_1h.csv by default
./target/release/backtester path/to/ohlc.csv # or pass a CSV as the first arg
```

The CSV must have a header and the columns `time,open,high,low,close` where `time` is UNIX seconds (UTC).

### Getting OHLC data

Three ways, in order of effort:

1. **Use the bundled sample** — `data/SOLUSDT_1h.csv` ships with the repo (SOL/USDT, 1h, 48 094 bars).
2. **Generate synthetic data** — `cargo run --release --example gen_synthetic` writes `data/SYNTHETIC.csv` (GBM-based OHLC, no network required). Handy for smoke-tests or reproducible demos.
3. **Download real data via the Python sibling** — the Rust binary reads the exact CSV format that the sibling project's [`binance_ohlc_downloader.py`](https://github.com/DaruFinance/quant-research-framework/blob/main/binance_ohlc_downloader.py) emits, so you can point it straight at a file you fetched there:
   ```bash
   python binance_ohlc_downloader.py --symbol DOGEUSDT --interval 30m --market spot --source api --since 2017-11-01 --until now --out /tmp/DOGEUSDT_30m.csv
   cargo run --release -- /tmp/DOGEUSDT_30m.csv
   ```

## What's Included

- **`src/lib.rs`** — Backtester engine. Pub types (`Bar`, `Trade`, `Metrics`, `Config`), indicator and metric primitives, the IS/OOS pipeline, smart-optimised look-back search with auto-RRR, candle- or trade-triggered walk-forward, robustness overlays (entry drift, fee shock, slippage shock, indicator variance), Monte Carlo diagnostics, and trade export. 1-to-1 port of `backtester.py`.
- **`src/main.rs`** — Reference strategy binary: EMA(20) vs EMA(lb) crossover, ~40 lines. This is the default you get from `cargo run --release`.
- **`examples/atr_cross.rs`** — Second strategy: ATR-cross with RSI≥50 confluence, matching the proprietary `ATR_x_EMA50_RSIge50` spec. Run with `cargo run --release --example atr_cross`.
- **`examples/gen_synthetic.rs`** — Synthetic OHLC generator (GBM, no network). Run with `cargo run --release --example gen_synthetic`.
- **`examples/README.md`** — Short tutorial on how to write your own strategy against the `RawSignalsFn` contract.
- **`data/SOLUSDT_1h.csv`** — Sample OHLC dataset (SOL/USDT, 1h candles) so both binaries run out-of-the-box.

## Adding your own strategy

A strategy is a single function:

```rust
fn my_strategy(bars: &[Bar], lb: usize) -> Vec<i8> {
    // compute indicators on `bars`, return +1/-1/0 per bar (no look-ahead).
}

fn main() {
    quant_research_framework_rs::run_with_csv("data/ohlc.csv", "my-strategy", my_strategy);
}
```

See [`examples/README.md`](examples/README.md) for the contract in detail and `examples/atr_cross.rs` for a worked example.

## Key Features

### Walk-Forward Evaluation
- Baseline IS/OOS test with raw default look-back
- Per-window re-optimisation and forward testing
- Configurable trigger: fixed candle count or fixed trade count per window
- Replication ratio printed before and after optimisation
- **Second OOS split** (`USE_OOS2`): doubles the final OOS so the
  framework reports both halves separately as an extra layer of OOS
  evidence.

### Realism Controls
- **Fees** and **slippage** applied on entry and exit
- **Funding fee** at 00:00, 08:00, 16:00 UTC (crypto). Gated by the new
  `USE_FOREX` toggle in v0.2.0 — set it to `true` and funding is
  skipped, matching FX broker semantics.
- **Stop-loss / take-profit** with intrabar high/low checks (no look-ahead)
- **Session mode** (`USE_SESSIONS`): restricts entries to a UTC window
  (`SESSION_START_HOUR..SESSION_END_HOUR`) and force-closes any open
  position on the last in-session bar of each day. Defaults to off so
  existing parity numbers are unaffected.

### Regime segmentation
- 3-regime EMA-based detector by default (Uptrend / Downtrend / Ranging)
- **Pluggable**: `RegimeConfig::new(labels, detector)` accepts any
  `RegimeDetectorFn = fn(&[Bar]) -> Vec<u8>` and `REGIME_LABELS` slice of
  length 2..5. See [`examples/regime_custom.rs`](examples/regime_custom.rs)
  for a 4-regime trend×volatility detector.
- Per-regime LB optimisation, OOS LB rotation, and per-regime RRR
  selection mirror the Python reference. Run via `run_with_regime` /
  `run_with_regime_cfg`.
- Cross-language parity for the regime path is verified by
  [`tools/parity_combo.py`](tools/parity_combo.py); track scope in
  [CHANGELOG.md](CHANGELOG.md).

### ML-driven strategies
- The signal contract `RawSignalsFn = fn(&[Bar], usize) -> Vec<i8>` is
  unchanged, so any model that produces per-bar long/short scores plugs
  in. Two patterns shipped:
  - [`examples/ml_precomputed.rs`](examples/ml_precomputed.rs) — train
    offline, plug a per-bar score slice into the strategy fn, threshold
    it. Fastest path; framework-agnostic.
  - [`examples/ml_callback.rs`](examples/ml_callback.rs) — keep a model
    in memory and call `predict(features)` per bar (online / stateful).
    Hand-coded linear model so the example has zero extra dependencies;
    swap for `linfa`, `smartcore`, `ort`, `tch`, or a Python FFI bridge.

### Robustness / Stress Tests
Configurable scenarios run against the optimised baseline and every WFO window:
- `ENTRY_DRIFT` — shift entries one bar forward
- `FEE_SHOCK` — 2× fees
- `SLIPPAGE_SHOCK` — 3× slippage
- `INDICATOR_VARIANCE` — ±1 perturbation on the selected look-back
- `NEWS_CANDLES_INJECTION` — synthetic high-vol wicks every 500–1000
  bars (added in v0.2.0; matches the Python reference's 5-scenario set)
- Any combination of the above

### Versioning

The crate follows [Semantic Versioning](https://semver.org/). See
[`CHANGELOG.md`](CHANGELOG.md) for what changed in each release.

## Parity with the Python reference

This repo is a clean-room port of the Python reference at
[`backtester/__init__.py`](https://github.com/DaruFinance/quant-research-framework/blob/main/backtester/__init__.py).
Three automated harnesses verify the port produces metrics that agree
with the reference within `1e-3` relative tolerance on shared input.
We avoid the term *byte-identical*: parity is tolerance-bounded by
construction (the maximum observed relative deviation across all
surfaces is below `5e-5`, the ledger's `%.4f` print precision floor),
not bit-equality.

### Default-config surface (`tools/parity_check.py`)
**56/56 metric points agree at 0.001 relative tolerance.** Covers the v0.1.0 feature set — IS/OOS baseline, smart-optimised look-back search with auto-RRR, candle/trade WFO, and the four v0.1.0 robustness scenarios (ENTRY_DRIFT, FEE_SHOCK, SLIPPAGE_SHOCK, INDICATOR_VARIANCE). Every `IS-raw`, `OOS-raw`, `IS-opt`, `OOS-opt`, `Baseline`, `ENT`, `FEE`, `SLI`, and `W01..W18 IS/OOS` line matches in trade count, ROI, PF, Sharpe, win rate, expectancy and max drawdown:

```bash
python tools/parity_check.py --tol 0.001    # exit 0 = parity
```

### Regime + WFO surface (`tools/parity_regime.py`)
**98/98 metric points agree at 0.001 relative tolerance.** Covers `USE_REGIME_SEG=True` + `USE_WFO=True` at otherwise-default settings: per-regime LB optimisation with RRR probe, OOS LB rotation, the 200-bar warmup, and four WFO windows on the bundled SOL CSV. See the CHANGELOG for the three regime-path bugs that closed the gap:

```bash
python tools/parity_regime.py --tol 0.001   # exit 0 = parity
```

### Forex-mode surface (`tools/parity_forex.py`, v0.3.1+)
**56/56 metric points agree at 0.001 relative tolerance** on the bundled EURUSD 1h CSV with `FOREX_MODE=True` on both engines:

```bash
python tools/parity_forex.py --tol 0.001    # exit 0 = parity
```

### What is *not* yet jointly validated

The full ledger of every surface — green-and-gated or known-divergence-with-reason — is **[docs/PARITY.md](docs/PARITY.md)**. The short version:

- **Four-way combo** (`tools/parity_combo.py`: regime + WFO + forex + session, all at once) still reports diffs — the gap is in the forex/session interaction (it shows up even on the classic `Baseline IS/OOS` line), not in the regime engine. Single-feature parity (default, regime+WFO, forex) is verified; the combo is the `paper-v3` milestone.
- **USD/JPY in the frozen benchmark** is excluded from the cross-engine gate (`cross_engine_check = false`): the JPY `pip_size = 0.01` path has a ~1–2% residual `parity_forex` (EUR/USD) does not exercise. NET/GROSS numbers are still reported.
- **Monte Carlo percentiles** and the **`INDICATOR_VARIANCE`** overlay diverge by design (unseeded / differently-seeded RNGs); see below.
- **Python-only, no Rust counterpart:** `backtester/bootstrap.py` (stationary bootstrap) and the `examples/ml_*` strategies. Nothing to diff, not gated.

### Two non-deterministic sections intentionally diverge, by design of the reference
1. **Monte Carlo percentiles** — Python uses NumPy's global RNG, Rust uses `StdRng` seeded to 42. Different algorithms, so percentiles differ; the distribution shape is the same.
2. **`INDICATOR_VARIANCE` overlay** — picks a ±1 lookback shift via an unseeded RNG in both implementations, so `W*_IS+ENT+IND` / `W*_OOS+ENT+IND` lines jitter run-to-run in both.

If you disable those two sources of randomness, the outputs are identical down to the last printed decimal on the validated surfaces above.

## Performance

Both implementations run the same default pipeline (IS/OOS baseline +
smart-optimiser + WFO + Monte Carlo + robustness overlays) on slices of the
bundled `SOLUSDT_1h.csv`, single-threaded. The published benchmark is
`tools/bench_paper.py` — the same harness, numbers, and methodology the
[paper](#citation) reports: **median** warm wall-clock over `n=5` runs after
one untimed warm-up (which absorbs Python's one-time Numba JIT cost), with a
separately-reported Python cold run, and peak RSS as the max observed.
Reproduce with:

```bash
python tools/bench_paper.py --runs 5
```

| Bars   | Python warm (s) | Rust (s) | Speed-up | Python RSS (MB) | Rust RSS (MB) |
|-------:|----------------:|---------:|---------:|----------------:|--------------:|
|  5,000 |    2.32 ± 0.06  |    0.01  |  232×†   |             270 |           2.8 |
| 15,000 |    2.85 ± 0.05  |    0.05  |  57.0×   |             273 |           4.2 |
| 30,000 |    3.98 ± 0.09  |    0.12  |  33.2×   |             280 |           6.2 |
| 48,000 |    5.71 ± 0.10  |    0.24  |  23.8×   |             294 |           8.8 |

So **23.8–57× faster** and **33–65× less memory** across the 15k–48k range.
†The 5,000-bar row is a measurement-floor artifact — Rust there (0.01 s) sits
at the `/usr/bin/time` resolution — so the 232× should not be over-interpreted.
The steady-state figure is the full 48k-bar row (23.8× faster, 33× less
memory); the band widens at smaller N as Python's fixed overhead dominates.
Rust's edge: no pandas, no NumPy, single-threaded with zero allocations in the
hot loop. (`tools/bench.py` is a lighter quick-check variant of the same
workload; `bench_paper.py` is the citable measurement.)

## Comparison vs other open-source backtesters

What this framework emphasises that mainstream open-source alternatives do
not (verified against primary docs as of 2026-04):

| Framework              | License                  | Built-in WFO | Per-regime LB optimisation | Strict-LAH property tests | Cross-language byte-parity tests |
|------------------------|--------------------------|:------------:|:--------------------------:|:-------------------------:|:--------------------------------:|
| **this** (Python + Rust) | Apache-2.0             | ✓            | ✓                          | ✓                         | ✓                                |
| [vectorbt][vbt]        | Apache-2.0 + Commons     | ✓ (Splitter) | ✗                          | ✗                         | n/a                              |
| [backtrader][bt]       | GPL-3.0                  | ✗ (community) | ✗                         | ✗                         | n/a                              |
| [NautilusTrader][nt]   | LGPL-3.0                 | ✗ (engine only) | ✗                       | ✗                         | ✗ (bilingual; no parity asserts) |
| [zipline-reloaded][zl] | Apache-2.0               | ✗ (3rd-party) | ✗                         | ✗                         | n/a                              |
| [QuantConnect Lean][lean] | Apache-2.0            | ✓            | ✗                          | ✗                         | n/a                              |
| [bt][btp]              | MIT                      | ✗            | ✗                          | ✗                         | n/a                              |

The **combination** is the contribution: WFO + per-regime LB + strict
no-look-ahead enforced by ledger-level invariant tests + a Python
reference and Rust port whose metric outputs agree within $10^{-3}$
relative tolerance on every validated surface, enforced continuously by the
`parity_*.py` harness suite in CI. Each cell individually exists somewhere; no
other framework ships the whole bundle.

[vbt]: https://github.com/polakowo/vectorbt
[bt]:  https://github.com/mementum/backtrader
[nt]:  https://github.com/nautechsystems/nautilus_trader
[zl]:  https://github.com/stefan-jansen/zipline-reloaded
[lean]: https://github.com/QuantConnect/Lean
[btp]: https://github.com/pmorissette/bt

## Configuration

Tunables are plain `const`s at the top of `src/main.rs` — edit and `cargo build --release` to apply. Names mirror the Python constants exactly:

| Const | Default | Notes |
|---|---|---|
| `CSV_FILE` | `data/SOLUSDT_1h.csv` | overridable via CLI arg |
| `ACCOUNT_SIZE` / `RISK_AMOUNT` | 100 000 / 2 500 | USD |
| `BACKTEST_CANDLES` / `OOS_CANDLES_BASE` | 10 000 / 90 000 | IS / OOS window sizes |
| `DEFAULT_LB` | 50 | lookback centre for the optimiser search range |
| `OPT_METRIC` | `"Sharpe"` | one of ROI, PF, Sharpe, WinRate, Exp, MaxDrawdown |
| `USE_SL` / `SL_PERCENTAGE` | true / 1.0 | stop-loss in % |
| `USE_TP_DEFAULT` / `TP_PERCENTAGE_DEFAULT` | true / 3.0 | take-profit in % |
| `OPTIMIZE_RRR` | true | auto-pick best R:R; classic optimiser searches {1,2,3}, regime-path optimiser searches {1..5} (mirrors the Python reference's split) |
| `USE_WFO` / `WFO_TRIGGER_MODE` / `WFO_TRIGGER_VAL` | true / candles / 5000 | walk-forward config |
| `USE_MONTE_CARLO` / `MC_RUNS` | true / 1000 | diagnostics on IS returns |
| `Config::use_regime_seg` | false (flipped to true by `run_with_regime_cfg`) | enables the 200-bar warmup in the backtest core; matches Python's `USE_REGIME_SEG` global |

## Citation

If you use this framework in academic or research work, please cite via
[`CITATION.cff`](CITATION.cff). The Python reference has its own
[`CITATION.cff`](https://github.com/DaruFinance/quant-research-framework/blob/main/CITATION.cff)
and citing either implies the other (sibling cross-reference).

## License

Apache-2.0 — see [LICENSE](LICENSE).
