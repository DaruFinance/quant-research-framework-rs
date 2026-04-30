#!/usr/bin/env python3
"""Paper-grade benchmark: extends tools/bench.py with N=7 runs, std/IQR
reporting, and a separate cold-Numba (first-call JIT compile) measurement
for the Python side. Used to populate the §Performance table in the
quant-research-framework paper.

Usage:
    python tools/bench_paper.py --runs 7 --out bench_paper.csv
"""
from __future__ import annotations
import argparse, json, os, re, shutil, statistics, subprocess, sys, tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))
DEFAULT_CSV = REPO_RUST / "data" / "SOLUSDT_1h.csv"
DEFAULT_SIZES = [5000, 15000, 30000, 48000]
DEFAULT_RUNS = 7

GNU_TIME = "/usr/bin/time"
TIME_FMT = "QRF_BENCH %e %M"
TIME_RE = re.compile(r"^QRF_BENCH\s+(?P<elapsed>[\d.]+)\s+(?P<rss_kb>\d+)\s*$",
                     re.MULTILINE)

PY_DRIVER = """
import sys
sys.path.insert(0, %r)
import backtester as bt
bt.PRINT_EQUITY_CURVE = False
import matplotlib
matplotlib.use("Agg")
bt.main()
"""

@dataclass
class Sample:
    elapsed_s: float
    peak_rss_mb: float

def time_cmd(cmd, env=None, cwd=None):
    p = subprocess.run([GNU_TIME, "-f", TIME_FMT] + cmd,
                       capture_output=True, text=True, env=env, cwd=cwd)
    if p.returncode != 0:
        sys.stderr.write(p.stderr); raise RuntimeError(f"failed: {cmd}")
    m = TIME_RE.search(p.stderr)
    if not m: raise RuntimeError(f"can't parse time: {p.stderr}")
    return Sample(float(m.group("elapsed")),
                  int(m.group("rss_kb")) / 1024.0)

def slice_csv(src, n, dst):
    with src.open() as f, dst.open("w") as g:
        g.write(f.readline())
        for i, line in enumerate(f):
            if i >= n: break
            g.write(line)

def summarise(label, samples):
    elapsed = sorted(s.elapsed_s for s in samples)
    rss = [s.peak_rss_mb for s in samples]
    n = len(elapsed)
    median = statistics.median(elapsed)
    mean = statistics.mean(elapsed)
    stdev = statistics.stdev(elapsed) if n > 1 else 0.0
    q1 = elapsed[n//4]; q3 = elapsed[(3*n)//4]
    sys.stderr.write(f"  [{label}] median={median:.3f}s  mean={mean:.3f}s  "
                     f"std={stdev:.3f}s  IQR=[{q1:.3f}, {q3:.3f}]  "
                     f"min={min(elapsed):.3f}s  max={max(elapsed):.3f}s  "
                     f"n={n}; max_rss={max(rss):.0f} MB\n")
    return {"label": label, "n": n, "elapsed_min": min(elapsed),
            "elapsed_median": median, "elapsed_mean": mean,
            "elapsed_std": stdev, "elapsed_q1": q1, "elapsed_q3": q3,
            "elapsed_max": max(elapsed), "rss_max": max(rss),
            "samples_s": [s.elapsed_s for s in samples],
            "samples_rss_mb": [s.peak_rss_mb for s in samples]}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--sizes", type=str,
                   default=",".join(str(s) for s in DEFAULT_SIZES))
    p.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    p.add_argument("--out", type=Path, default=None,
                   help="path to write JSON results")
    a = p.parse_args()

    if not REPO_PY.exists() or not shutil.which("cargo"):
        sys.stderr.write("setup error\n"); return 2

    binary = REPO_RUST / "target" / "release" / "backtester"
    if not binary.exists():
        subprocess.run(["cargo","build","--release"], cwd=REPO_RUST, check=True)

    sizes = [int(s) for s in a.sizes.split(",")]
    out = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for n in sizes:
            sliced = td / f"bench_{n}.csv"
            slice_csv(a.csv, n, sliced)
            sys.stderr.write(f"\n=== N={n} bars ===\n")

            # Python: 1 cold run (Numba JIT), then `--runs` warm
            py_env = os.environ.copy()
            py_env.update({"BT_CSV": str(sliced), "MPLBACKEND": "Agg",
                           "PYTHONUNBUFFERED": "1"})
            py_cmd = [sys.executable, "-c", PY_DRIVER % str(REPO_PY)]
            sys.stderr.write(f"  Python cold (JIT compile)...\n")
            py_cold = time_cmd(py_cmd, env=py_env)
            sys.stderr.write(f"    cold: {py_cold.elapsed_s:.3f}s, "
                             f"{py_cold.peak_rss_mb:.0f} MB\n")
            py_warm = []
            for r in range(a.runs):
                s = time_cmd(py_cmd, env=py_env)
                sys.stderr.write(f"    warm {r+1}/{a.runs}: "
                                 f"{s.elapsed_s:.3f}s, {s.peak_rss_mb:.0f} MB\n")
                py_warm.append(s)
            py_summary = summarise("py-warm", py_warm)
            py_summary["cold_s"]  = py_cold.elapsed_s
            py_summary["cold_rss_mb"] = py_cold.peak_rss_mb

            # Rust: warm-up, then `--runs` timed (no JIT, but cache warm)
            sys.stderr.write(f"  Rust warm-up...\n")
            time_cmd([str(binary), str(sliced)], cwd=REPO_RUST)
            rs = []
            for r in range(a.runs):
                s = time_cmd([str(binary), str(sliced)], cwd=REPO_RUST)
                sys.stderr.write(f"    rs {r+1}/{a.runs}: "
                                 f"{s.elapsed_s:.3f}s, {s.peak_rss_mb:.0f} MB\n")
                rs.append(s)
            rs_summary = summarise("rust", rs)

            out.append({"bars": n, "python": py_summary, "rust": rs_summary})

    print(json.dumps(out, indent=2))
    if a.out:
        a.out.write_text(json.dumps(out, indent=2) + "\n")
        sys.stderr.write(f"\n[bench_paper] wrote {a.out}\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
