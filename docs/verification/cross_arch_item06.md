# Verification log — Roadmap item 06: cross-architecture parity

**Date:** 2026-05-31
**Result:** aarch64 reproduces the x86_64 deterministic metric block
**byte-for-byte** across all six bundled datasets. **1,176 metric lines
(6 × 196), zero diffs.**

## What was run

Host: x86_64 (WSL2). Cross toolchain: `aarch64-unknown-linux-gnu` target,
`aarch64-linux-gnu-gcc` linker, `qemu-aarch64-static` user-mode emulation
(`QEMU_LD_PREFIX=/usr/aarch64-linux-gnu`). Rust 1.94.0.

```
# x86_64 native binary
cargo build --release
file target/release/backtester
  -> ELF 64-bit ... x86-64

# aarch64 cross-build (release profile: opt-level 3, lto, codegen-units 1)
CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc \
  cargo build --release --target aarch64-unknown-linux-gnu
file target/aarch64-unknown-linux-gnu/release/backtester
  -> ELF 64-bit ... ARM aarch64

# goldens emitted from the x86_64 binary
python tools/parity_arch.py --emit-golden --bin target/release/backtester
  -> 6 emitted (196 metric lines each)

# aarch64 binary under QEMU, byte-identical vs the x86_64 goldens
QEMU_LD_PREFIX=/usr/aarch64-linux-gnu python tools/parity_arch.py \
  --bin target/aarch64-unknown-linux-gnu/release/backtester \
  --runner qemu-aarch64-static
  -> OK SOLUSDT_1h / BTCUSDT_30m / DOGEUSDT_30m / EURUSD_1h / USDJPY_1h /
     SYNTH_100k : byte-identical (196 lines each)
  -> parity_arch: 6 checked, 0 bad -> OK
```

The metric block compared is every line of the shape
`<tag> | Trades:.. ROI:.. PF:.. Shp:.. Win:.. Exp:.. MaxDD:..` — the
IS/OOS-raw, IS/OOS-opt, Baseline, all five robustness scenarios (ENT, FEE,
SLI, NEWS-candle, indicator-variance), and the per-window WFO summaries.
Wall-clock/load timing lines are excluded (non-deterministic). The
comparison is exact string equality, strictly stronger than the 1e-3
cross-language tolerance.

## Why bit-identical is achievable (not just tolerance-bounded)

- The hot path uses only `.powi(2)` and `.sqrt()` — both IEEE-754
  correctly-rounded and identical on x86_64 and aarch64. No `fma` /
  `mul_add`, no fast-math, no `target-feature` flags, no `.cargo/config`
  codegen overrides.
- The one historical cross-arch non-determinism — the `±1` indicator-
  variance LB perturbation propagating to a different optimised parameter
  across hosts — was fixed by seeding it (`IND_VARIANCE_SEED=42`,
  `src/lib.rs:101`, commit `26766d9`).

## Artifacts

| File | Purpose |
|---|---|
| `tools/dump_metrics.sh` | dump the deterministic metric block of a binary on every dataset |
| `tools/parity_arch.py` | emit x86_64 goldens / assert byte-identity for a target binary |
| `data/golden/*.x86_64.txt` | committed x86_64 reference metric blocks (6 files) |
| `.github/workflows/parity_arm64.yml` | CI: emit-golden (drift guard) + qemu-aarch64 + native-aarch64 |

## Scope note

The default `backtester` binary (USE_WFO=true, default config) is the
surface proven here. The regime and forex example binaries share the same
numeric kernel; extending the byte-identity matrix to them is a cheap
follow-up (run the same `parity_arch.py` against those example binaries).
