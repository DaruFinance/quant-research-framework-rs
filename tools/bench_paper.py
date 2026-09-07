#!/usr/bin/env python3
"""Run the current 150k, ten-configuration WFO benchmark once per engine.

This replaces the old sliced-data, pseudo-warm benchmark. It runs one fresh
process per engine, includes Python's first Numba compilation, excludes Rust
compilation, and keeps every trade ledger for verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_RUST = Path(__file__).resolve().parents[1]
REPO_PY = Path(os.environ.get("QRF_PY_DIR", REPO_RUST.parent / "quant-research-framework"))
TIME = "/usr/bin/time"
TIME_FORMAT = "wall_s=%e,user_s=%U,sys_s=%S,max_rss_kb=%M,cpu_pct=%P"
THREAD_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "NUMBA_NUM_THREADS": "1",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def timed(command: list[str], sidecar: Path, *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(
        [TIME, "-o", str(sidecar), "-f", TIME_FORMAT, *command],
        check=True,
        cwd=cwd,
        env=env,
    )


def parse_time(path: Path) -> dict[str, float | int | str]:
    fields = dict(item.split("=", 1) for item in path.read_text().strip().split(","))
    return {
        "wall_s": float(fields["wall_s"]),
        "user_s": float(fields["user_s"]),
        "sys_s": float(fields["sys_s"]),
        "max_rss_kb": int(fields["max_rss_kb"]),
        "cpu_pct": fields["cpu_pct"],
    }


def verify(python_result: dict, rust_result: dict) -> dict:
    assert python_result["bars"] == rust_result["bars"] == 150_000
    assert python_result["strategies"] == rust_result["strategies"] == 10
    left = {row["name"]: row for row in python_result["results"]}
    right = {row["name"]: row for row in rust_result["results"]}
    assert left.keys() == right.keys()
    max_delta = 0.0
    for name in left:
        py, rs = left[name], right[name]
        assert py["windows"] == rs["windows"] == 28
        assert py["ledger_rows"] == rs["ledger_rows"]
        for key in ("trades", "roi", "pf", "sharpe", "max_drawdown"):
            a, b = py["metrics"][key], rs["metrics"][key]
            if a is None or b is None:
                assert a is b
            else:
                delta = abs(a - b)
                max_delta = max(max_delta, delta)
                assert delta <= 1e-9, f"{name}: {key} differs by {delta}"
    return {
        "status": "match",
        "strategies": len(left),
        "windows_each": 28,
        "max_metric_abs_delta": max_delta,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--python-repo", type=Path, default=REPO_PY)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    if args.out.exists():
        raise FileExistsError(f"output directory already exists: {args.out}")
    args.out.mkdir(parents=True)
    if not args.skip_build:
        subprocess.run(
            [
                "cargo",
                "build",
                "--locked",
                "--release",
                "--example",
                "wfo_benchmark",
                "--features",
                "indicators",
            ],
            check=True,
            cwd=REPO_RUST,
        )

    env = os.environ.copy()
    env.update(THREAD_ENV)
    python_out = args.out / "python"
    rust_out = args.out / "rust"
    python_time = args.out / "python.time.txt"
    rust_time = args.out / "rust.time.txt"

    timed(
        [
            sys.executable,
            str(REPO_RUST / "tools" / "bench_python_wfo.py"),
            "--python-repo",
            str(args.python_repo),
            "--csv",
            str(args.csv),
            "--out",
            str(python_out),
        ],
        python_time,
        cwd=REPO_RUST,
        env=env,
    )
    timed(
        [
            str(REPO_RUST / "target" / "release" / "examples" / "wfo_benchmark"),
            str(args.csv),
            str(rust_out),
        ],
        rust_time,
        cwd=REPO_RUST,
        env=env,
    )

    python_result = json.loads((python_out / "results.json").read_text())
    rust_result = json.loads((rust_out / "results.json").read_text())
    summary = {
        "dataset": {"path": str(args.csv), "sha256": sha256(args.csv)},
        "boundary": (
            "one fresh process per engine; ten serial 150k WFO configurations; "
            "Python first JIT included; Rust compilation excluded"
        ),
        "thread_env": THREAD_ENV,
        "python": parse_time(python_time),
        "rust": parse_time(rust_time),
        "validation": verify(python_result, rust_result),
    }
    summary["python_over_rust_wall_ratio"] = (
        summary["python"]["wall_s"] / summary["rust"]["wall_s"]
    )
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
