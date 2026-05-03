# Contributing to quant-research-framework-rs

This is the Rust port of
[`quant-research-framework`](https://github.com/DaruFinance/quant-research-framework)
(the Python reference). The Python reference is **ground truth**. This
port exists to make batch sweeps fast at strict semantic equivalence
to the reference; no Rust-only feature lands here without a matching
Python implementation.

## The parity invariant

> Any change that alters engine semantics must keep the metric-output
> diff against the Python reference within $10^{-3}$ relative tolerance
> on the three published surfaces (default-config, regime+WFO, forex).

If your change is intentional and changes outputs, the Python reference
must land the matching change *first*; the port follows. The tools/
diff harnesses are how we verify the follow-through.

If your change is **not** intended to alter outputs (refactor, doc,
type cleanup, an algorithmic speedup that's bit-equivalent), the parity
diff must still pass at or below the current tolerance.

## Four-command verification checklist

From this repo's root, with `quant-research-framework` checked out as a
sibling directory at the matching tag:

```sh
# 1. Cargo's own test surface (behavioural + invariant tests).
cargo test --release

# 2. Default-config parity (56 metric points, 8 stages, SOLUSDT 1h).
python tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001

# 3. Regime + WFO parity (98 metric points, 14 stages, SOLUSDT 1h).
python tools/parity_regime.py --csv data/SOLUSDT_1h.csv --tol 0.001

# 4. Forex-mode parity (56 metric points, 8 stages, EURUSD 1h).
python tools/parity_forex.py --csv data/EURUSD_1h.csv --tol 0.001
```

All four must pass. The Python framework must be importable from the
sibling directory; CI builds the matching commit automatically.

## Deliberate-bug ablations

The `tools/ablations/` directory contains the bug-injection scripts
that produce Table 3 in the paper. They edit `src/lib.rs` on a
throwaway branch, run `parity_check.py`, capture the result, and
restore the source. If you change `src/lib.rs` in a way that moves
those reported numbers, regenerate the table via:

```sh
bash tools/ablations/fee_bias.sh
bash tools/ablations/fill_off_by_one.sh
bash tools/ablations/funding_skip.sh
```

## What kinds of changes are welcome

- **Strategy implementations** in `src/main.rs` matching the
  `RawSignalsFn` contract; bundled examples live under `examples/`.
- **Algorithmic improvements** to indicator computation that are
  semantically identical to pandas (the parity diff is the gate).
- **Tests** under `tests/`, especially behavioural-equivalence tests
  against fixed Python-reference outputs.
- **Documentation** of any kind.

## What requires extra care

- **`backtest_core`, `parse_signals_for`, `optimiser`**: engine
  internals. A change usually shifts metric outputs and requires a
  matching Python change.
- **`Config`**: the configuration surface is part of the public
  contract. Adding a field is a minor version; renaming or removing
  one is a major version.
- **Floating-point reductions**: `f64` reduction order matters. Prefer
  Kahan summation or a documented order if the diff against pandas
  vectorised reductions starts to drift past $10^{-7}$ relative.

## Code style

- `cargo fmt` before commit (CI checks this).
- `cargo clippy` clean (CI fails on warnings in the lib target).
- No SIMD intrinsics. The §9 limitation in the paper is intentional;
  if you want SIMD, open an issue and we'll discuss the parity
  implications first.
- Prefer `Result` propagation over `panic!` in library code; CLI
  binaries may use `expect` with a clear message.

## Releases

- Update `CHANGELOG.md` for any user-visible behaviour change.
- Bump the version in `Cargo.toml` and `CITATION.cff`.
- Tag the release with the version suffix matching the Python
  framework's coordinated tag (e.g. Rust `v0.3.1` ↔ Python `v0.2.5`).
