# Deliberate-bug ablation harness

Reproduces the §6.3 *tolerance sensitivity* table in the paper:

> A. Reproducibility-First Walk-Forward Backtester with Tolerance-Bounded
> Cross-Language Parity (Vieira Gatto, 2026).

Each script applies a minimal-diff bug to `src/lib.rs` on a throwaway
branch, runs `cargo build --release`, runs `tools/parity_check.py`
against the unchanged Python reference, captures the mismatch count
and maximum relative deviation, and restores the source.

## Reproducing the paper table

Run, from the repo root:

```sh
bash tools/ablations/fee_bias.sh        # 4 rows: 0% / 0.01% / 0.1% / 1%
bash tools/ablations/fill_off_by_one.sh # 1 row: +1 fill index
bash tools/ablations/funding_skip.sh    # 1 row: missed funding accrual
```

Each script prints a single line per row in CSV format
(`bug_class, mismatches, max_rel_dev_pct, result`) suitable for
pasting into Table 3.

## Safety

- Each script begins with `git diff --quiet src/lib.rs ||
  { echo "src/lib.rs has uncommitted changes; aborting"; exit 1; }`
  so it cannot silently destroy your in-progress work.
- Each script ends with `git checkout -- src/lib.rs && cargo build
  --release` so the binary is restored to the clean state on exit.
- Run on a feature branch you don't mind throwing away.

## Bug definitions

| Script | What changes | Why this is the bug to test |
|---|---|---|
| `fee_bias.sh` | `FEE_PCT_DEFAULT` constant | Smooth, monotonic — calibrates the discrimination threshold |
| `fill_off_by_one.sh` | `bars[idx].open` → `bars[(idx+1).min(...)].open` in `backtest_core` | Textbook look-ahead-direction off-by-one; exercises the core temporal contract |
| `funding_skip.sh` | Comment out `funding_acc += fee_f` | Silent omission of a small recurring cost; exercises the cumulative-bias case |

The expected output (paper Table 3, paper-v1 retag):

```
fee_0pct, 0, <5e-5, OK
fee_0.01pct, 0, <5e-5, OK
fee_0.1pct, 1, 0.16, FAIL
fee_1pct, 21, 0.88, FAIL
fill_off_by_one, 54, 179.6, FAIL
funding_skip, 34, 8.86, FAIL
```
