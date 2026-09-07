# Bar-permutation Monte Carlo

Completed-trade `permutation` shuffles without replacement; `resampling` samples with replacement. Both default to 1,000 runs and accept a custom count. Bar permutation defaults to 500 runs because every iteration rebuilds OHLCV and reruns the frozen strategy.

Each invocation tests a frozen strategy specification against reconstructed OHLCV
paths. Each iteration independently shuffles close log returns and complete
intrabar templates without replacement, keeps timestamps fixed, moves volume
with its template, regenerates strategy signals and runs the full backtest
without re-optimizing parameters.

Bar permutation is expensive because every iteration executes another full
backtest. The default is 500 iterations. Start with one full-history run to
measure local cost, then choose a worker count from 1 to 4:

```bash
python mc_bar_permutation/run.py \
  --mode bar-permutation \
  --input data/SOLUSDT_1h.csv \
  --output results/mc-bars \
  --runs 500 \
  --workers 1
```

Completed-trade Monte Carlo uses the same dispatcher:
`python mc_bar_permutation/run.py --mode resampling --returns returns.json --output results/mc-trades`.
Use `--mode permutation` as the alternative. Trade modes default to 1,000 runs.

Before timing the queue, the script builds the Rust worker. Build files stay
under the output directory unless `--worker-bin` is provided.

Each run writes a native binary trade ledger, metrics and a status record.
`manifest.json` records source and parameter hashes, requested and completed
counts, seeds, failures and output hashes. Completed runs with the same source, specification and seed are reused on restart.

A failed run makes the command fail. No p-value is reported from an incomplete
queue.

`spec.example.json` freezes the built-in EMA-crossover lookback, disables SL/TP
and keeps fees, slippage and funding enabled. The shipped worker rejects unknown strategy names. A custom Rust
strategy can call `permute_bars()` and `run_frozen_backtest()` with its own
`RawSignalsFn`; this recompiles the worker rather than substituting saved
historical signals.

Raw signals contain `-1`, `0` or `1`; zero means hold the current target.

The logarithmic reconstruction requires positive finite OHLC. Negative or zero
prices, including negative-price WTI histories, are rejected rather than
dropped or converted to missing values. This mode is a no-replacement bar
permutation, not the stationary bar-return bootstrap used in the later gold
Monte Carlo paper.
