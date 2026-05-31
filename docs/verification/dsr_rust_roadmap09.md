# Verification log — Roadmap item 09: DSR mirror in Rust

**Date:** 2026-05-31
**Scope:** Port `backtester/dsr.py` (Deflated Sharpe Ratio, Bailey & López
de Prado 2014) to a Rust mirror with cross-language parity. Public-roadmap
item 09 / paper-v3 milestone.

## Artifacts landed (in `quant-research-framework-rs-v2`)

| File | Change |
|---|---|
| `src/dsr.rs` | new — `expected_max_sharpe_under_null`, `deflated_sharpe_ratio`, `report`, 6 unit tests |
| `src/lib.rs` | `#[cfg(feature = "dsr")] pub mod dsr;` |
| `Cargo.toml` | new `dsr = ["dep:statrs"]` feature + `[[example]] _parity_dsr` |
| `examples/_parity_dsr.rs` | new — scalar parity driver (reads `|`-delimited fixture, emits `key=value`) |
| `tools/parity_dsr.py` | new — cross-language harness, 11 cases / 22 metric points |

## Design decisions

- **`Phi` / `Phi^{-1}` source:** `statrs::distribution::Normal::{cdf,
  inverse_cdf}` (matches scipy `norm.cdf` / `norm.ppf` to ~1e-12). The
  coarse A&S 7.1.26 `erf` in `t5_statarb::screen` (max err 1.5e-7, no
  inverse) was deliberately **not** reused — its tail error would corrupt
  `SR_0` at the `1 - 1/N` quantile.
- **Feature gating:** standalone `dsr` feature pulling only `statrs`, so
  DSR is usable without the panel/pairs/t5-statarb stack — mirrors how
  Python ships `backtester.dsr` as a thin module. `statrs` was already a
  declared optional dep, so the paper's "three direct runtime deps" claim
  is unaffected.
- **Moment conventions replicated exactly:** trial-Sharpe variance and the
  return std use `ddof = 1` (`numpy.var/std(ddof=1)`); `g_3`/`g_4` use the
  population mean of `z^k` (division by `t`, matching `np.mean(z**k)`).
  `g_4` is **raw** kurtosis (= 3 for Normal), per the Bailey-LdP eq. (9)
  `(g_4 - 1)` coefficient — not excess kurtosis.
- All degenerate guards return the same value as Python (`0.0` for
  `N < 2` / zero trial variance; `NaN` for `t < 3`, non-finite chosen
  Sharpe, `sd <= 0`, `denom_sq <= 0`).

## Results

```
cargo test --release --features dsr --lib dsr      -> 6 passed; 0 failed
cargo clippy --release --features dsr              -> 0 warnings (dsr code)
cargo build --release  (no dsr feature)            -> unaffected, clean

python tools/parity_dsr.py                         -> 22/22 within tol=1e-9 -> OK
python tools/parity_dsr.py --tol 0.001             -> 22/22 within tol=1e-3 -> OK
```

The golden unit test pins the real Python output for a fixed input
(`sr0 = 0.3979082244143515`, `dsr = 0.9301178821563774`) to < 1e-9.

## One documented cross-library artifact

Case `neg_sharpe` produces a deep negative `z_hat`; scipy `norm.cdf`
returns a denormal `7.25e-15` where statrs flushes to exactly `0.0`. The
**absolute** gap is f64 tail noise (`7e-15`); only the relative metric
diverges because the denominator is ~0. The harness applies an absolute
floor `--atol 1e-12` (the same `ε` underflow-guard philosophy as the
paper's metamorphic relation, which uses `max(|a|,|b|,ε)`). This is not a
port discrepancy — the DSR arithmetic is identical; it is the underlying
normal-CDF library's denormal-tail behaviour.

## Not done here (deliberate — needs Daniel / release coordination)

- **Public promotion.** The Python `dsr.py` is already byte-identical on
  the public `quant-research-framework` repo (no change needed). The Rust
  side must be promoted to the public `quant-research-framework-rs` repo
  (monolithic `src/lib.rs` — decide inline vs. first `src/` submodule) at
  the coordinated release tag.
- **Paper edit.** §9 still says "DSR is Python-only at paper-v2 … Rust
  mirror tracked for paper-v3." Update once promoted.
- No commit/push performed — left in the working tree for review.
