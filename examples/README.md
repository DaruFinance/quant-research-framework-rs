# Examples: adding your own strategy

The whole backtester — IS/OOS split, optimiser, walk-forward, robustness,
MC, trade export — lives in `src/lib.rs`. A **strategy** is the one function
that takes OHLC bars and returns raw long/short intents. Everything else is
shared.

```rust
pub type RawSignalsFn = fn(&[Bar], usize) -> Vec<i8>;
```

That's the entire contract. Write a function with that signature, hand it to
`run` or `run_with_csv`, and you have a backtested strategy.

## The contract

Your function returns a `Vec<i8>` the same length as `bars`, where:

| Value | Meaning |
|------:|---------|
| `+1`  | **Long intent** at this bar — open or hold a long |
| `-1`  | **Short intent** at this bar — open or hold a short |
|  `0`  | **No intent** at this bar (typically: indicator not warmed up yet, or no crossover this bar) |

The `lb` argument is the look-back length the optimiser is sweeping over
(`25..76` by default, centred on `DEFAULT_LB = 50`). Any indicator parameter
that you want the optimiser to optimise should scale with `lb`.

### No look-ahead

`raw[i]` must only use information available **at or before bar `i-1`**.
In practice that means either indexing your indicator with `[i-1]`, or
calling `.shift(1)` in pandas terms. The library uses `raw[i]` to set the
desired position *at bar i*, and executes the trade at `bars[i].open`.

### Entries vs levels

Two equivalent ways to express a crossover strategy:

1. **Sign-of-difference** (dense). `raw[i] = +1` whenever `fast[i-1] > slow[i-1]`,
   `-1` whenever `fast[i-1] < slow[i-1]`. `parse_signals` picks out the
   flips. This is what `src/main.rs` does for EMA-crossover.

2. **Cross-events** (sparse). `raw[i] = +1` **only** at the bar of a
   cross-up (`fast[i-1] > slow[i-1] && fast[i-2] <= slow[i-2]`), `-1` only
   at a cross-down, `0` in between. This is what the proprietary
   `run_strategies.py` does and what the ATR example here does.

Both produce the same trades — `parse_signals` detects the first `+1`/`-1`
after a position change — but the sparse form is tidier when you want to
stack a confluence filter on top.

### Adding a confluence

A "confluence" is just a boolean filter you multiply your signal by before
returning. Any extra indicator you'd like — RSI threshold, volatility
floor, higher-timeframe agreement — is a line of code in your strategy
function. See `atr_cross.rs` for an RSI≥50 example.

## Running

```bash
# Reference strategy (EMA crossover) — this is `src/main.rs`
cargo run --release

# ATR-cross with RSI confluence — this is `examples/atr_cross.rs`
cargo run --release --example atr_cross

# Point either one at a different CSV
cargo run --release -- path/to/ohlc.csv
cargo run --release --example atr_cross -- path/to/ohlc.csv
```

Every strategy prints the same blocks (IS/OOS raw + optimised, Replication
ratios, four Robustness scenarios, up to 18 WFO windows, optional Monte
Carlo, WFO summary) and emits the same `trade_list.csv` format.

## Writing your own

1. Copy `atr_cross.rs` to `examples/my_strategy.rs`.
2. Replace the indicator helpers and the body of the raw-signals function.
   Keep the signature `fn(&[Bar], usize) -> Vec<i8>`.
3. In `main`, call
   `run_with_csv("data/your.csv", "my-strategy", my_strategy);`
4. `cargo run --release --example my_strategy`

If you need an indicator that isn't in `src/lib.rs`, just write it inline —
the ATR, SMA, EWM, and RSI helpers in `atr_cross.rs` are ~80 lines total
and are mirror-images of the Python equivalents in `indicators_tradingview.py`
in the sibling Python repo.
