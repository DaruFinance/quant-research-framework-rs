# Quant Research Backtester — Rust port

A faithful Rust port of the [**quant-research-framework**](https://github.com/DaruFinance/quant-research-framework) Python backtester: walk-forward optimization (WFO), robustness stress tests, and realism controls (fees, slippage, funding, SL/TP), with the same strategy logic and the same numeric output as the reference Python implementation.

> Does an apparent edge survive **out-of-sample** evaluation under realistic frictions — or is it just fitting the past? Same question, same method, running ~24× faster and in a fraction of the memory.

## Quick Start

```bash
cargo build --release
./target/release/backtester                 # uses data/SOLUSDT_1h.csv by default
./target/release/backtester path/to/ohlc.csv # or pass a CSV as the first arg
```

The CSV must have a header and the columns `time,open,high,low,close` where `time` is UNIX seconds (UTC). This is exactly the format produced by the Python sibling project's `binance_ohlc_downloader.py`.

## What's Included

- **`src/main.rs`** — Main research backtester. 1-to-1 port of `backtester.py`:
  - In-sample (IS) vs out-of-sample (OOS) split with `iloc`-style negative indexing
  - Smart-optimised look-back search (neighbourhood PF sanity check)
  - Auto risk-to-reward ratio (RRR) selection
  - Rolling walk-forward optimisation, candle- or trade-triggered
  - Robustness overlays: entry drift, fee shock, slippage shock, indicator variance
  - Monte Carlo bootstrap/shuffle diagnostics
  - Trade export to `trade_list.csv`
- **`data/SOLUSDT_1h.csv`** — Sample OHLC dataset (SOL/USDT, 1h candles) so the backtester runs out-of-the-box.

## Key Features

### Walk-Forward Evaluation
- Baseline IS/OOS test with raw default look-back
- Per-window re-optimisation and forward testing
- Configurable trigger: fixed candle count or fixed trade count per window
- Replication ratio printed before and after optimisation

### Realism Controls
- **Fees** and **slippage** applied on entry and exit
- **Funding fee** at 00:00, 08:00, 16:00 UTC (crypto; skipped in forex mode, which is off by default here)
- **Stop-loss / take-profit** with intrabar high/low checks (no look-ahead)

### Robustness / Stress Tests
Configurable scenarios run against the optimised baseline and every WFO window:
- `ENTRY_DRIFT` — shift entries one bar forward
- `FEE_SHOCK` — 2× fees
- `SLIPPAGE_SHOCK` — 3× slippage
- `INDICATOR_VARIANCE` — ±1 perturbation on the selected look-back
- Any combination of the above

## Parity with the Python reference

This repo is a line-by-line port of [`backtester.py`](https://github.com/DaruFinance/quant-research-framework/blob/main/backtester.py). Running both on the same CSV with matching config (`USE_MONTE_CARLO=False`) produces identical deterministic output — every `IS-raw`, `OOS-raw`, `IS-opt`, `OOS-opt`, `Baseline`, `ENT`, `FEE`, `SLI`, and `W01..W18 IS/OOS` line matches byte-for-byte in the trade count, ROI, PF, Sharpe, win rate, expectancy and max drawdown.

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
