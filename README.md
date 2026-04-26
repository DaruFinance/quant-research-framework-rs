# Quant Research Backtester — Rust port

A faithful Rust port of the [**quant-research-framework**](https://github.com/DaruFinance/quant-research-framework) Python backtester: walk-forward optimization (WFO), robustness stress tests, and realism controls (fees, slippage, funding, SL/TP), with the same strategy logic and the same numeric output as the reference Python implementation.

> Does an apparent edge survive **out-of-sample** evaluation under realistic frictions — or is it just fitting the past? Same question, same method, running ~24× faster and in a fraction of the memory.

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
- v0.2.0 ships the **contract** (`RegimeDetectorFn = fn(&[Bar]) -> Vec<u8>`,
  `REGIME_LABELS` const slice up to length 5) so user code can already
  adopt the same shape as the Python reference. See
  [`examples/regime_custom.rs`](examples/regime_custom.rs) for a
  4-regime trend×volatility detector.
- The **engine** (per-regime LB optimisation, OOS LB rotation,
  regime-aware filters) is scheduled for v0.3.0; setting
  `USE_REGIME_SEG = true` today still hits the 200-bar warmup stub.
  Track progress in [CHANGELOG.md](CHANGELOG.md).

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

This repo is a line-by-line port of [`backtester.py`](https://github.com/DaruFinance/quant-research-framework/blob/main/backtester.py). For the **`v0.1.0` feature set** — IS/OOS baseline, smart-optimised look-back search with auto-RRR, candle/trade WFO, and the four `v0.1.0` robustness scenarios (ENTRY_DRIFT, FEE_SHOCK, SLIPPAGE_SHOCK, INDICATOR_VARIANCE) — running both on the same CSV with matching config (`USE_MONTE_CARLO=False`) produces identical deterministic output: every `IS-raw`, `OOS-raw`, `IS-opt`, `OOS-opt`, `Baseline`, `ENT`, `FEE`, `SLI`, and `W01..W18 IS/OOS` line matches byte-for-byte in the trade count, ROI, PF, Sharpe, win rate, expectancy and max drawdown.

The **`v0.2.0` additions** — `USE_FOREX`, session mode, `NEWS_CANDLES_INJECTION`, the regime-detector contract, the WFO+regime fix in Python — are present in both implementations but have not yet been jointly validated by an automated parity harness; that harness is being staged for a follow-up release. See [CHANGELOG.md](CHANGELOG.md) for the precise scope of each.

Two non-deterministic sections intentionally diverge, by design of the reference:

1. **Monte Carlo percentiles** — Python uses NumPy's global RNG, Rust uses `StdRng` seeded to 42. Different algorithms, so percentiles differ; the distribution shape is the same.
2. **`INDICATOR_VARIANCE` overlay** — picks a ±1 lookback shift via an unseeded RNG in both implementations, so `W*_IS+ENT+IND` / `W*_OOS+ENT+IND` lines jitter run-to-run in both.

If you disable those two sources of randomness, the outputs are identical down to the last printed decimal.

## Performance

Both implementations run the full pipeline — IS/OOS baseline + smart-optimiser + 4 robustness scenarios + 18 WFO windows with 4 robustness overlays each — on the sample `SOLUSDT_1h.csv` (48,094 bars). Measured on a WSL2 Linux shell, 3 runs back-to-back:

| Metric          | Python (numba)   | Rust (release)  | Ratio   |
|-----------------|------------------|-----------------|---------|
| Wall time       | ~5.2 s           | ~0.22 s         | **~24× faster** |
| User CPU time   | ~8.7 s           | ~0.10 s         | ~80×    |
| Peak RSS        | ~268 MB          | ~5 MB           | ~53× less |

The Python figure includes numba's cached JIT startup, which dominates at small runtimes. Rust wins more the larger the dataset — the steady-state kernel is tight and single-threaded with no allocations in the hot loop.

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
| `OPTIMIZE_RRR` | true | auto-pick best R:R in {1,2,3} |
| `USE_WFO` / `WFO_TRIGGER_MODE` / `WFO_TRIGGER_VAL` | true / candles / 5000 | walk-forward config |
| `USE_MONTE_CARLO` / `MC_RUNS` | true / 1000 | diagnostics on IS returns |

## License

MIT — see [LICENSE](LICENSE).
