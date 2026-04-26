#!/usr/bin/env python3
"""Cross-language benchmark harness.

Runs the Rust binary and the Python backtester on the same OHLC CSV
across several dataset sizes, reports min wall-clock and peak RSS.
Default config (USE_WFO=true, OPTIMIZE_RRR=true, USE_MONTE_CARLO=true,
no regime/forex/session) on both sides — same code path either engine
hits when you `cargo run --release` / `python backtester.py`.

Usage:
    python tools/bench.py                         # default sizes, 3 runs each
    python tools/bench.py --sizes 5000,15000      # custom sizes
    python tools/bench.py --runs 5                # more samples
    python tools/bench.py --csv path/to/file.csv  # custom dataset

Outputs a markdown table to stdout (drop straight into README).
Requires GNU `time` at /usr/bin/time (Linux/WSL).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get(
    "QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))

DEFAULT_CSV = REPO_RUST / "data" / "SOLUSDT_1h.csv"
DEFAULT_SIZES = [5000, 15000, 30000, 48000]
DEFAULT_RUNS = 3

GNU_TIME = "/usr/bin/time"
TIME_FMT = "QRF_BENCH %e %M"  # elapsed-seconds  max-rss-kb
TIME_RE = re.compile(r"^QRF_BENCH\s+(?P<elapsed>[\d.]+)\s+(?P<rss_kb>\d+)\s*$",
                     re.MULTILINE)


@dataclass
class Sample:
    elapsed_s: float
    peak_rss_mb: float


def slice_csv(src: Path, n_rows: int, dst: Path) -> None:
    with src.open() as f, dst.open("w") as g:
        header = f.readline()
        g.write(header)
        for i, line in enumerate(f):
            if i >= n_rows:
                break
            g.write(line)


def time_command(cmd: list[str], env: dict | None = None,
                 cwd: Path | None = None) -> Sample:
    full = [GNU_TIME, "-f", TIME_FMT] + cmd
    proc = subprocess.run(full, capture_output=True, text=True,
                          env=env, cwd=cwd)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise RuntimeError(f"command failed: {' '.join(cmd)}")
    m = TIME_RE.search(proc.stderr)
    if m is None:
        raise RuntimeError(f"could not parse /usr/bin/time output:\n{proc.stderr}")
    return Sample(
        elapsed_s=float(m.group("elapsed")),
        peak_rss_mb=int(m.group("rss_kb")) / 1024.0,
    )


def bench_engine(label: str, cmd: list[str], runs: int,
                 env: dict | None = None,
                 cwd: Path | None = None) -> Sample:
    samples = []
    for r in range(runs):
        s = time_command(cmd, env=env, cwd=cwd)
        samples.append(s)
        sys.stderr.write(
            f"  [{label} run {r+1}/{runs}] {s.elapsed_s:.2f}s, "
            f"{s.peak_rss_mb:.0f} MB\n")
    elapsed = min(s.elapsed_s for s in samples)
    rss = max(s.peak_rss_mb for s in samples)
    return Sample(elapsed_s=elapsed, peak_rss_mb=rss)


PY_DRIVER = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
import matplotlib
matplotlib.use("Agg")
bt.main()
"""


def python_cmd(csv: Path) -> tuple[list[str], dict]:
    env = os.environ.copy()
    env["BT_CSV"] = str(csv)
    env["MPLBACKEND"] = "Agg"
    env["PYTHONUNBUFFERED"] = "1"
    driver = PY_DRIVER % str(REPO_PY)
    return [sys.executable, "-c", driver], env


def rust_cmd(csv: Path, binary: Path) -> list[str]:
    return [str(binary), str(csv)]


def ensure_rust_binary() -> Path:
    binary = REPO_RUST / "target" / "release" / "backtester"
    if not binary.exists():
        sys.stderr.write("[bench] cargo build --release ...\n")
        subprocess.run(["cargo", "build", "--release"], cwd=REPO_RUST,
                       check=True)
    return binary


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV,
                   help=f"OHLC CSV (default: {DEFAULT_CSV})")
    p.add_argument("--sizes", type=str,
                   default=",".join(str(s) for s in DEFAULT_SIZES),
                   help="comma-separated list of bar counts")
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS,
                   help=f"runs per size per engine (default: {DEFAULT_RUNS})")
    p.add_argument("--out", type=Path, default=None,
                   help="optional path to write the markdown table")
    args = p.parse_args()

    if not shutil.which("cargo"):
        sys.stderr.write("error: cargo not on PATH\n")
        return 2
    if not REPO_PY.exists():
        sys.stderr.write(f"error: Python repo not found at {REPO_PY}\n"
                         "       set QRF_PY_DIR or clone the sibling repo\n")
        return 2
    if not args.csv.exists():
        sys.stderr.write(f"error: CSV not found: {args.csv}\n")
        return 2

    sizes = [int(s) for s in args.sizes.split(",")]
    binary = ensure_rust_binary()

    rows = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for n in sizes:
            sliced = td / f"bench_{n}.csv"
            slice_csv(args.csv, n, sliced)

            sys.stderr.write(f"\n[bench] N={n} bars -> {sliced.name}\n")

            sys.stderr.write(f"[bench] Python warm-up...\n")
            py_cmd, py_env = python_cmd(sliced)
            time_command(py_cmd, env=py_env)
            sys.stderr.write(f"[bench] Python timing ({args.runs} runs)\n")
            py = bench_engine("python", py_cmd, args.runs, env=py_env)

            sys.stderr.write(f"[bench] Rust warm-up...\n")
            rs_cmd = rust_cmd(sliced, binary)
            time_command(rs_cmd)
            sys.stderr.write(f"[bench] Rust timing ({args.runs} runs)\n")
            rs = bench_engine("rust", rs_cmd, args.runs)

            rows.append((n, py, rs))

    lines = []
    lines.append("| Bars   | Python (s) | Rust (s) | Speed-up | Python RSS (MB) | Rust RSS (MB) |")
    lines.append("|-------:|-----------:|---------:|---------:|----------------:|--------------:|")
    for n, py, rs in rows:
        speedup = py.elapsed_s / rs.elapsed_s if rs.elapsed_s > 0 else float("inf")
        lines.append(
            f"| {n:>6,} | {py.elapsed_s:>10.2f} | {rs.elapsed_s:>8.2f} | "
            f"{speedup:>7.2f}× | {py.peak_rss_mb:>15.0f} | {rs.peak_rss_mb:>13.0f} |"
        )
    table = "\n".join(lines)
    print(table)
    if args.out:
        args.out.write_text(table + "\n")
        sys.stderr.write(f"\n[bench] wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
