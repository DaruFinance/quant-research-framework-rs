# Verification log — Roadmap item 08: four-way parity (regime + WFO + forex + session)

**Date:** 2026-05-31
**Status:** PARTIAL — dominant cause fixed; narrow trade-count residual remains.

## Finding

The combo harness (`tools/parity_combo.py`) was a known-failing diagnostic.
Root-causing the divergence:

1. **Dominant cause (FIXED).** The Python combo driver overrode
   `MIN_TRADES = 1` and `OPTIMIZE_RRR = False`, but on the Rust side these are
   **compile-time constants** (`MIN_TRADES = 10`, `OPTIMIZE_RRR = true`). A
   parity comparison must feed identical knobs to both engines; the override
   made the IS phase select a different lookback, cascading into ~2× trade
   divergence on every stage. The single-feature harnesses
   (`parity_check`/`regime`/`forex`) pass precisely because they do NOT touch
   these (they use the shared defaults, which match the Rust consts). Aligning
   the combo driver to `MIN_TRADES = 10` / `OPTIMIZE_RRR = True` collapses the
   divergence: on EURUSD 1h, ROI / PF / Sharpe / WinRate now fall mostly within
   the 1e-3..few-% band.

2. **Residual (OPEN).** Exact trade COUNTS still differ ~5–10% — even on
   `IS-raw`, which involves no LB optimization (py=75 vs rs=79 on EURUSD). The
   divergence is therefore in raw signal→trade conversion under the
   session+forex overlay, not in the optimiser. Rust consistently emits a few
   MORE trades, pointing at the **session re-entry semantics** (a position
   dropped at an out-of-session bar and re-snapped at the next in-session bar
   may count as a new trade in one engine but a continuation in the other) or a
   **DST-day session-boundary** off-by-one (pandas `tz_convert` vs chrono-tz).

## Next step to close

Dump the in-session mask + the session-aware `parse_signals` entry/exit codes
from both engines on EURUSD 1h, diff bar-by-bar to find the first divergent
bar (expected on a session boundary or a US DST-transition day), and align
whichever engine is wrong — WITHOUT changing the single-feature session
behaviour (re-run `parity_regime`/`parity_forex` after any engine edit to
prove no regression). Only then promote `parity_combo` to a registry-based
1e-3 gate and wire it into `parity.yml`.

## Harness changes committed

- `tools/parity_combo.py`: aligned `MIN_TRADES`/`OPTIMIZE_RRR` to the Rust
  constants (the dominant-cause fix); added `--csv` so the combo can run on
  EURUSD 1h (forex pip-sizing is economically meaningful there, unlike on a
  crypto series).
