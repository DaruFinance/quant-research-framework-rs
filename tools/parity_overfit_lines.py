#!/usr/bin/env python3
"""Guard: assert NO overfitting-report line matches parity_common.LINE_RE.

The overfit report's safety rests on a single invariant: none of its lines
carry the `| Trades: ... ROI: ... PF: ... Shp: ... Win: ...% Exp: ...
MaxDD:` metric body that LINE_RE matches (the leading indent does NOT
protect them, LINE_RE starts with ^\\s*). This converts that fragile,
prose-asserted invariant into an enforced gate: a future edit that adds a
Trades: field to an overfit line is caught here, before it can perturb the
existing parity harnesses.

Exit 0 = surface clean, 1 = a line leaked into the parity surface.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(
    os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework")
)
sys.path.insert(0, str(REPO_PY))
sys.path.insert(0, str(REPO_RUST))

from tools.parity_common import LINE_RE  # noqa: E402
from backtester import dsr, haircut       # noqa: E402

rng = np.random.default_rng(0)
samples = [
    dsr.report(0.9, list(np.full(24, 0.3)),
               list(rng.normal(0, 0.01, 300))),
    haircut.report(1.5, 252, 24, "bhy", 252.0),
    "  ---- Overfitting diagnostics (opt-in; non-parity lines) ----",
    "  INFO | effective trials N=24 (distinct strategies, NOT windows*combos)  |  OOS bars T=500  |  SR_chosen(sqrtT)=0.9000",
    "  WARN | SHARPE_MODE='bar': trial Sharpes are annualised; read DSR/PSR with care.",
    "  PBO  | S=16  splits=12870  N=8  T=200  PBO=0.123",
    "  PSR  | SR_chosen:  0.90  SR*: 0.00  P(SR>SR*):0.873",
    "  MTRL | target_conf=0.95  SR*: 0.00  min_obs=128.3  (have 500)",
    "  MBTL | N=24  SR_target(per-bar):0.0900  min_obs=18.4  (have 500)",
    "  -------------------------------------------------------------",
]

leaked = [s for s in samples if LINE_RE.match(s) is not None]
if leaked:
    for s in leaked:
        print(f"  OVERFIT LINE LEAKED INTO PARITY SURFACE: {s!r}")
    print("parity_overfit_lines: FAIL")
    raise SystemExit(1)

print(f"parity_overfit_lines: {len(samples)}/{len(samples)} lines ignored by "
      f"LINE_RE -> OK (parity surface clean)")
raise SystemExit(0)
