#!/usr/bin/env python3
"""Generate data/volume_fixture.csv (6-col OHLCV) for the volume strategy
family and tools/parity_volume.py. Deterministic random walk on a 1h grid,
strictly increasing timestamps (load_ohlc's sort is then a no-op, so the
Python reset-flag order matches the Rust file order — see parity_volume.py
monotonicity assert). Volume is positive lognormal with periodic spikes so
relative-volume / z-score / MFI have signal. EVERY volume cell is populated
(no empty cells), keeping the lenient empty->0.0 parse off the parity surface.

Writes to BOTH repos so Rust examples and the Python mirror load the same file.

    python tools/make_volume_fixture.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = REPO_RUST.parent / "quant-research-framework"

N = 1500
START = 1609459200  # 2021-01-01 00:00:00 UTC, spans many NY days incl DST
STEP = 3600


def build() -> str:
    rng = np.random.default_rng(20260611)
    rows = ["time,open,high,low,close,volume"]
    price = 100.0
    for i in range(N):
        t = START + i * STEP
        drift = 0.0002 * math.sin(i / 50.0)
        ret = drift + rng.normal(0.0, 0.004)
        o = price
        c = o * (1.0 + ret)
        hi = max(o, c) * (1.0 + abs(rng.normal(0.0, 0.0015)))
        lo = min(o, c) * (1.0 - abs(rng.normal(0.0, 0.0015)))
        base = math.exp(rng.normal(6.0, 0.4))
        spike = 4.0 if (i % 37 == 0) else 1.0
        vol = base * spike  # always > 0, always populated
        rows.append(f"{t},{o:.6f},{hi:.6f},{lo:.6f},{c:.6f},{vol:.6f}")
        price = c
    return "\n".join(rows) + "\n"


def main() -> int:
    content = build()
    for repo in (REPO_RUST, REPO_PY):
        out = repo / "data" / "volume_fixture.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        print(f"wrote {out} ({N} bars, 6-col OHLCV)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
