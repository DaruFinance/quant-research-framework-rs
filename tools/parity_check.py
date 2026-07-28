#!/usr/bin/env python3
"""Cross-language parity harness: quant-research-framework (Python) vs
quant-research-framework-rs (Rust). Runs both engines on the same
deterministic CSV and compares the per-tag IS/OOS metric lines.

Usage
-----

    python tools/parity_check.py                       # bundled SOL CSV
    python tools/parity_check.py --tol 0.001           # 0.1% rel tolerance
    python tools/parity_check.py --csv path/to/ohlc.csv
    python tools/parity_check.py --include costs sortino   # opt in forward families

The shared regex / subprocess / comparison machinery lives in
``tools/parity_common.py``; this script is a thin driver that selects
the ``base`` metric family from ``tools/parity_registry.json``.

refactor: behaviour is bit-identical to the pre-#15 single-
file script when invoked without ``--include``. The 56/56 default
parity claim is unchanged.

Exit code 0 = within tolerance; 1 = mismatch; 2 = setup failure.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import parity_common as pc


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=None,
                   help="OHLC CSV path (default: REPO_RUST/data/SOLUSDT_1h.csv)")
    p.add_argument("--tol", type=float, default=0.001,
                   help="relative tolerance for metric comparison (default 0.1%%)")
    p.add_argument("--include", nargs="*", default=[],
                   help="opt-in metric families beyond 'base' "
                        "(costs, sortino, panel, pairs, carry, multi-leg)")
    args = p.parse_args()

    if not pc.REPO_PY.exists():
        sys.stderr.write(
            f"Python repo not found at {pc.REPO_PY}\n"
            f"Set QRF_PY_DIR or check out the sibling repo there.\n"
        )
        return 2

    csv = args.csv or pc.REPO_RUST / "data" / "SOLUSDT_1h.csv"
    if not csv.exists():
        sys.stderr.write(f"need CSV at {csv}\n")
        return 2

    registry = pc.MetricRegistry.load()
    families = pc.resolve_families(registry, "base", args.include)
    tags = registry.union_tags(families)
    fields = registry.union_fields(families)

    print(f"Python repo : {pc.REPO_PY}")
    print(f"Rust  repo  : {pc.REPO_RUST}")
    print(f"CSV         : {csv}")
    print(f"Families    : {families}")
    print(f"Tolerance   : {args.tol*100:.3f}%")

    print("\nRunning Python...")
    py = pc.parse_metrics(pc.run_python(csv))
    print(f"  parsed {len(py)} tagged lines")
    print("Running Rust...")
    rs = pc.parse_metrics(pc.run_rust_default_binary(csv))
    print(f"  parsed {len(rs)} tagged lines")

    diffs = pc.compare(py, rs, tags, fields, args.tol)
    return 0 if diffs == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
