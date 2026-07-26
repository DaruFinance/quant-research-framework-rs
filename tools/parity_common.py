"""Shared parity-script primitives.

The three default parity scripts (``parity_check.py``,
``parity_regime.py``, ``parity_forex.py``) all do essentially the same
thing: run the Python and Rust engines, regex-extract per-tag metric
tuples from stdout, and compare them field-by-field at a configurable
relative tolerance. This module centralises that shared logic so each
parity script becomes a thin driver that only specifies:

- which family or families from `parity_registry.json` to apply,
- which Python-side `bt.*` overrides to set in the subprocess driver,
- which Rust binary or one-off `examples/*.rs` runner to invoke.

The registry (loaded from ``tools/parity_registry.json``) lists
metric **families** — each family is a set of tag whitelist entries
plus the field list to diff. Default invocations check the ``base``
family only, preserving the 210-points-at-1e-3 claim today.

``--include`` adds opt-in families that exist for forward
compatibility: ``costs`` (item #3 stdout exposure when ``record_costs``
flips on), ``sortino``, ``panel`` (item #1+#4+#5),
``pairs`` (Phase 3), ``carry`` (Phase 3). None of these emit new
stdout lines yet; the registry slots are placeholders that will be
populated as the corresponding items land.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(
    os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework")
)

REGISTRY_PATH = REPO_RUST / "tools" / "parity_registry.json"

LINE_RE = re.compile(
    r"^\s*(?P<tag>[A-Za-z0-9_+\-:,]+(?:\s+[A-Za-z0-9_+\-:,]+)?)\s+"
    r"(?:\(LB[^)]*\)\s*)?\|\s*"
    r"Trades:\s*(?P<trades>\-?\d+)\s+"
    r"ROI:\s*\$?(?P<roi>\-?[\d,]+\.\d+)R?\s+"
    r"PF:\s*(?P<pf>\-?[\d.]+|inf)\s+"
    r"Shp:\s*(?P<shp>\-?[\d.]+)\s+"
    r"Win:\s*(?P<win>\-?[\d.]+)%\s+"
    r"Exp:\s*\$?(?P<exp>\-?[\d,]+\.\d+)R?\s+"
    r"MaxDD:\s*\$?(?P<dd>\-?[\d,]+\.\d+)R?",
)


def parse_metrics(stdout: str) -> Dict[str, Dict[str, float]]:
    """Extract per-tag metric tuples from an engine's stdout."""
    out: Dict[str, Dict[str, float]] = {}
    for raw_line in stdout.splitlines():
        m = LINE_RE.match(raw_line)
        if not m:
            continue
        out[m.group("tag").strip()] = {
            "trades":   int(m.group("trades")),
            "roi":      float(m.group("roi").replace(",", "")),
            "pf":       float("inf") if m.group("pf") == "inf" else float(m.group("pf")),
            "sharpe":   float(m.group("shp")),
            "win_rate": float(m.group("win")) / 100.0,
            "exp":      float(m.group("exp").replace(",", "")),
            "max_dd":   float(m.group("dd").replace(",", "")),
        }
    return out


@dataclass
class MetricFamily:
    """One entry in ``parity_registry.json``."""
    name: str
    tags: List[str]
    fields: List[str] = field(
        default_factory=lambda: [
            "trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"
        ]
    )


class MetricRegistry:
    """In-memory view of ``parity_registry.json``. Single source of
    truth for parity tag whitelists and field sets per feature family.
    """

    def __init__(self, families: Iterable[MetricFamily]) -> None:
        self._families: Dict[str, MetricFamily] = {f.name: f for f in families}

    def __contains__(self, name: str) -> bool:
        return name in self._families

    def family(self, name: str) -> MetricFamily:
        if name not in self._families:
            raise KeyError(
                f"unknown metric family {name!r}; "
                f"have {sorted(self._families.keys())}"
            )
        return self._families[name]

    def names(self) -> List[str]:
        return sorted(self._families.keys())

    def union_tags(self, family_names: Iterable[str]) -> List[str]:
        """Ordered de-duplicated tag list from the named families."""
        seen: set[str] = set()
        out: List[str] = []
        for name in family_names:
            for tag in self.family(name).tags:
                if tag not in seen:
                    seen.add(tag)
                    out.append(tag)
        return out

    def union_fields(self, family_names: Iterable[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for name in family_names:
            for fld in self.family(name).fields:
                if fld not in seen:
                    seen.add(fld)
                    out.append(fld)
        return out

    @classmethod
    def load(cls, path: Path = REGISTRY_PATH) -> "MetricRegistry":
        data = json.loads(path.read_text())
        families = [
            MetricFamily(
                name=entry["name"],
                tags=list(entry.get("tags", [])),
                fields=list(entry.get(
                    "fields",
                    ["trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"]
                )),
            )
            for entry in data["families"]
        ]
        return cls(families)


def run_python(csv: Path, overrides: Optional[Dict[str, str]] = None,
               extra_setup: str = "") -> str:
    """Invoke ``bt.main()`` in a subprocess with optional config overrides.

    ``overrides`` maps ``bt.X = Y`` constants to assign before
    ``bt.main()``. ``extra_setup`` is appended verbatim — useful for
    derived assignments like ``bt.SL_PERCENTAGE *= bt.PIP_SIZE``.
    """
    env = os.environ.copy()
    env["BT_CSV"] = str(Path(csv).resolve())
    env["MPLBACKEND"] = "Agg"
    override_lines = "\n".join(
        f"bt.{k} = {v}" for k, v in (overrides or {}).items()
    )
    driver = f"""
import sys
sys.path.insert(0, {str(REPO_PY)!r})
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
bt.USE_MONTE_CARLO   = False
{override_lines}
{extra_setup}
bt.main()
"""
    proc = subprocess.run(
        [sys.executable, "-c", driver],
        env=env, cwd=REPO_PY, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Python run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    return proc.stdout


def run_rust_default_binary(csv: Path) -> str:
    """Invoke ``target/release/backtester`` on ``csv``."""
    bin_path = REPO_RUST / "target" / "release" / "backtester"
    if not bin_path.exists():
        build = subprocess.run(
            ["cargo", "build", "--release"],
            cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
        )
        if build.returncode != 0:
            sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n")
            sys.exit(2)
    proc = subprocess.run(
        [str(bin_path), str(Path(csv).resolve())],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    return proc.stdout


def run_rust_example(example_name: str, src_path: Path, src_body: str,
                     csv: Path) -> str:
    """Write a one-off ``examples/<example_name>.rs`` file, build it,
    run it. Used by parity_regime and parity_forex to spin up
    config-specific runners without polluting the public examples."""
    src_path.write_text(src_body)
    build = subprocess.run(
        ["cargo", "build", "--release", "--example", example_name],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=600,
    )
    if build.returncode != 0:
        sys.stderr.write(f"Rust build failed:\n{build.stderr[-2000:]}\n")
        sys.exit(2)
    bin_path = REPO_RUST / "target" / "release" / "examples" / example_name
    proc = subprocess.run(
        [str(bin_path), str(Path(csv).resolve())],
        cwd=REPO_RUST, capture_output=True, text=True, timeout=900,
    )
    if proc.returncode != 0:
        sys.stderr.write(f"Rust run failed:\n{proc.stderr[-2000:]}\n")
        sys.exit(2)
    return proc.stdout


def compare(py: Dict[str, Dict[str, float]],
            rs: Dict[str, Dict[str, float]],
            tags: List[str], fields: List[str], tol: float) -> int:
    """Diff Python and Rust metric dicts on the given tag set.

    Returns the number of mismatched fields (0 = parity OK).
    """
    diffs = 0
    print(f"\nMetric comparison ({len(tags)} tags x {len(fields)} fields, "
          f"tol={tol*100:.3f}%):\n")
    for tag in tags:
        present_py, present_rs = tag in py, tag in rs
        if not present_py and not present_rs:
            continue
        if not present_py:
            print(f"  [{tag}]  rust-only:  {rs[tag]}")
            diffs += 1
            continue
        if not present_rs:
            print(f"  [{tag}]  py-only:    {py[tag]}")
            diffs += 1
            continue
        print(f"  [{tag}]")
        for fld in fields:
            p = py[tag].get(fld)
            r = rs[tag].get(fld)
            if p is None or r is None:
                continue
            if fld == "trades":
                ok = p == r
                marker = "OK" if ok else "DIFF"
                print(f"    {fld:>8}: py={p}  rs={r}  [{marker}]")
                if not ok:
                    diffs += 1
                continue
            denom = max(abs(p), abs(r), 1e-9)
            rel = abs(p - r) / denom
            ok = rel <= tol or (abs(p) < 1e-6 and abs(r) < 1e-6)
            marker = "OK" if ok else "DIFF"
            print(
                f"    {fld:>8}: py={p:>14.4f}  rs={r:>14.4f}  "
                f"rel={rel:6.2%}  [{marker}]"
            )
            if not ok:
                diffs += 1
    print(f"\n  {'PARITY OK' if diffs == 0 else f'PARITY DIFF: {diffs}'}")
    return diffs


def resolve_families(registry: MetricRegistry, base_family: str,
                     include: Iterable[str]) -> List[str]:
    """Combine the script's mandatory base family with --include opt-ins.

    Each name in ``include`` must exist in the registry; unknown names
    raise ``KeyError`` to fail loudly rather than silently widen the
    gate.
    """
    out = [base_family]
    for name in include:
        if name == base_family:
            continue
        registry.family(name)  # raises if unknown
        out.append(name)
    return out
