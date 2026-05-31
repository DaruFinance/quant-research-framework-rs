# Verification log — Roadmap item 08: four-way parity (regime + WFO + forex + session)

**Date:** 2026-05-31
**Status:** ✅ CLOSED — byte-exact on EURUSD 1h across all stages × 7 fields.

## Result

```
python tools/parity_combo.py            # EURUSD 1h, tol 1e-3
  -> COMBO PARITY OK (10 stages x 7 fields within tol), exit 0
     (IS/OOS-raw, IS/OOS-opt, Baseline IS/OOS, W01-W04 IS/OOS all 0.00%,
      exact trade counts)
```
Regression check after the engine edit — the three published surfaces still pass:
`parity_check` (default) OK · `parity_regime` (regime+WFO) OK · `parity_forex` OK.
Full Rust suite: 41 tests pass; clippy clean.

## Root cause (two parts)

The combo was a known-failing diagnostic. Two independent causes:

1. **Harness knob mismatch (dominant).** The Python combo driver overrode
   `MIN_TRADES = 1` / `OPTIMIZE_RRR = False`, but on the Rust side these are
   compile-time constants (`MIN_TRADES = 10`, `OPTIMIZE_RRR = true`). A parity
   comparison must feed identical knobs to both engines; the override made the
   in-sample phase select a different lookback, cascading into ~2× divergence.
   Fix: align the combo driver to the shared defaults (10 / true). The
   single-feature harnesses pass precisely because they never touch these.

2. **Session-end-bar handling in the Rust backtest core (three sub-bugs).**
   Diagnosis: the in-session masks were proven byte-identical between the two
   engines (0 diffs over 53,160 EURUSD bars), and `parse_signals` is logically
   identical — so the divergence was in `backtest_core` when session and forex
   were both active (session had never been parity-tested in isolation; the
   combo is its first test). Against Python `_backtest_numba_core`, the Rust
   side on the last in-session bar of the day:
     - kept `code != 3` / `code != 1` guards on the force-close, so an
       opposite-flip signal **entered a new position** instead of force-closing;
     - had **no flat-entry block** (Python's `if use_sessions and code in (1,3)
       and end_bar_flag: code = 0`), so it opened positions on the closing bar
       Python suppresses;
     - ran the **intrabar SL/TP check** on the closing bar, whereas Python skips
       it (`not end_bar_flag`) and lets the force-close exit at the open.
   All three inflated the Rust trade count. Fix (`src/lib.rs`, `backtest_core`):
   on a session-end bar, force-close unconditionally if a position is open
   (`code = 2/4`), else block the entry (`code = 0`), and skip the SL/TP check.
   The change is fully guarded by `use_sessions`, so the non-session surfaces
   (parity_check/regime/forex) are byte-unchanged — confirmed by the regression
   run above.

## What shipped

- `src/lib.rs` — session-end handling in `backtest_core` rewritten to match
  Python exactly (see above).
- `tools/parity_combo.py` — promoted from diagnostic to a strict GATE: defaults
  to EURUSD 1h at 1e-3, exits non-zero on any mismatch (trade counts exact),
  `--csv`/`--tol`; docstring rewritten.
- This log.

## Remaining (rides on the v0.5.x coordinated release)

- Wire `parity_combo.py` into `parity.yml` in both public repos as the fourth
  gate (alongside check/regime/forex/ledger).
- Paper §9: flip the four-way limitation to "validated"; add the combo row to
  the parity table / metric-point total.
