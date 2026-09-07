#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from common import atomic_json, sha256


DEFAULT_RUNS = 500
MAX_WORKERS = 4
ALLOWED_CONFIG = {
    "account_size", "position_size", "fee_pct", "slippage_pct",
    "funding_fee", "use_sl", "sl_percentage", "use_tp", "tp_percentage",
    "forex_mode", "max_hold_bars", "sharpe_mode",
}


def build_worker(repo: Path, target: Path, example: str = "bar_permutation_worker") -> Path:
    environment = os.environ.copy()
    environment["CARGO_TARGET_DIR"] = str(target)
    subprocess.run(
        ["cargo", "build", "--release", "--example", example],
        cwd=repo,
        check=True,
        env=environment,
    )
    suffix = ".exe" if os.name == "nt" else ""
    return target / "release" / "examples" / f"{example}{suffix}"


def load_spec(path: Path) -> tuple[dict, dict]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    unknown_top = sorted(set(spec) - {"strategy", "config"})
    if unknown_top:
        raise ValueError(f"unsupported top-level fields: {', '.join(unknown_top)}")
    strategy = dict(spec.get("strategy", {}))
    if strategy.get("kind") != "ema-crossover":
        raise ValueError(
            f"unsupported strategy kind {strategy.get('kind')!r}; add a RawSignalsFn to a custom worker"
        )
    if strategy.get("parameters", {}):
        raise ValueError("ema-crossover does not accept strategy.parameters")
    lookback = int(strategy.get("lookback", 0))
    if lookback <= 0:
        raise ValueError("strategy.lookback must be positive")
    config = dict(spec.get("config", {}))
    unknown_config = sorted(set(config) - ALLOWED_CONFIG)
    if unknown_config:
        raise ValueError(f"unsupported config fields: {', '.join(unknown_config)}")
    required = ALLOWED_CONFIG - {"max_hold_bars", "sharpe_mode"}
    missing = sorted(required - set(config))
    if missing:
        raise ValueError(f"missing config fields: {', '.join(missing)}")
    return strategy, config


def complete_status(
    run_dir: Path, source_hash: str, spec_hash: str, seed: int, worker_hash: str,
) -> bool:
    path = run_dir / "status.json"
    if not path.exists():
        return False
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    metrics = run_dir / "metrics.json"
    ledger = run_dir / "ledger.bin"
    if not metrics.is_file() or not ledger.is_file():
        return False
    return (
        status.get("status") == "complete"
        and status.get("source_sha256") == source_hash
        and status.get("spec_sha256") == spec_hash
        and status.get("seed") == seed
        and status.get("worker_sha256") == worker_hash
        and status.get("metrics_sha256") == sha256(metrics)
        and status.get("ledger_sha256") == sha256(ledger)
    )


def run_one(
    command: list[str], environment: dict[str, str], run_dir: Path,
    seed: int, source_hash: str, spec_hash: str, worker_hash: str,
) -> dict:
    for name in ("metrics.json", "ledger.bin", "status.json"):
        (run_dir / name).unlink(missing_ok=True)
    started = time.perf_counter()
    process = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment
    )
    if process.returncode != 0:
        status = {
            "status": "failed", "seed": seed, "returncode": process.returncode,
            "elapsed_seconds": time.perf_counter() - started,
            "stderr": process.stderr[-4000:],
        }
    else:
        metrics = run_dir / "metrics.json"
        ledger = run_dir / "ledger.bin"
        status = {
            "status": "complete", "seed": seed,
            "source_sha256": source_hash, "spec_sha256": spec_hash,
            "metrics_sha256": sha256(metrics), "ledger_sha256": sha256(ledger),
            "ledger_file": ledger.name, "worker_sha256": worker_hash,
            "elapsed_seconds": time.perf_counter() - started,
        }
    atomic_json(run_dir / "status.json", status)
    return status


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run expensive full-backtest bar permutations with frozen strategy parameters."
    )
    parser.add_argument("--mode", choices=("permutation", "resampling", "bar-permutation"),
                        default="bar-permutation")
    parser.add_argument("--input")
    parser.add_argument("--returns", help="JSON array or one-return-per-line file for trade modes")
    parser.add_argument("--spec", default=str(Path(__file__).with_name("spec.example.json")))
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--worker-bin")
    parser.add_argument("--forex", action="store_true", help="use absolute-R trade drawdown")
    args = parser.parse_args()
    if args.runs is None:
        args.runs = DEFAULT_RUNS if args.mode == "bar-permutation" else 1_000
    if args.runs <= 0:
        parser.error("--runs must be positive")
    if not 1 <= args.workers <= MAX_WORKERS:
        parser.error(f"--workers must be between 1 and {MAX_WORKERS}")
    if args.seed < 0 or args.seed + args.runs - 1 > 2**64 - 1:
        parser.error("seed range must fit unsigned 64-bit integers")

    root = Path(__file__).resolve().parent
    repo = root.parent
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.mode != "bar-permutation":
        if not args.returns:
            parser.error("--returns is required for permutation and resampling")
        returns_path = Path(args.returns).resolve()
        worker = Path(args.worker_bin).resolve() if args.worker_bin else build_worker(
            repo, output / ".cargo-target", "monte_carlo_worker"
        )
        command = [
            str(worker), "--returns", str(returns_path), "--mode", args.mode,
            "--runs", str(args.runs), "--seed", str(args.seed),
            "--forex", str(args.forex).lower(), "--output", str(output / "manifest.json"),
        ]
        subprocess.run(command, check=True)
        return
    if not args.input:
        parser.error("--input is required for bar-permutation")
    source = Path(args.input).resolve()
    spec_path = Path(args.spec).resolve()
    strategy, config = load_spec(spec_path)
    source_hash = sha256(source)
    spec_hash = sha256(spec_path)
    worker = Path(args.worker_bin).resolve() if args.worker_bin else build_worker(
        repo, output / ".cargo-target"
    )
    environment = os.environ.copy()
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        environment[name] = "1"

    base = [
        str(worker), "--input", str(source),
        "--strategy", strategy["kind"], "--lookback", str(strategy["lookback"]),
        "--fee-pct", str(config["fee_pct"]),
        "--slippage-pct", str(config["slippage_pct"]),
        "--funding-fee", str(config["funding_fee"]),
        "--use-sl", str(config["use_sl"]).lower(),
        "--sl-percentage", str(config["sl_percentage"]),
        "--use-tp", str(config["use_tp"]).lower(),
        "--tp-percentage", str(config["tp_percentage"]),
        "--account-size", str(config["account_size"]),
        "--position-size", str(config["position_size"]),
        "--forex", str(config["forex_mode"]).lower(),
        "--max-hold-bars", str(config.get("max_hold_bars", 0)),
        "--sharpe-mode", str(config.get("sharpe_mode", "trade")),
    ]
    worker_hash = sha256(worker)
    statuses = {}
    pending = []
    for index in range(args.runs):
        seed = args.seed + index
        run_dir = output / "runs" / f"{index:06d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        if complete_status(run_dir, source_hash, spec_hash, seed, worker_hash):
            statuses[index] = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
        else:
            pending.append((index, seed, run_dir, base + ["--seed", str(seed), "--output", str(run_dir)]))

    print(
        f"bar-permutation is expensive: {args.runs} complete strategy backtests "
        f"over the full input, workers={args.workers}",
        flush=True,
    )
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(
                run_one, command, environment, run_dir, seed, source_hash, spec_hash, worker_hash
            ): (index, seed)
            for index, seed, run_dir, command in pending
        }
        for future in concurrent.futures.as_completed(future_map):
            index, _ = future_map[future]
            statuses[index] = future.result()
            completed = sum(value.get("status") == "complete" for value in statuses.values())
            failed = sum(value.get("status") == "failed" for value in statuses.values())
            print(f"finished={len(statuses)}/{args.runs} complete={completed} failed={failed}", flush=True)

    ordered = [statuses[index] for index in range(args.runs)]
    completed = sum(status.get("status") == "complete" for status in ordered)
    failed = args.runs - completed
    atomic_json(output / "manifest.json", {
        "mode": "bar-permutation",
        "method": "independent no-replacement shuffles of close log returns and OHLCV templates",
        "timestamps": "preserved",
        "volume": "moves with its OHLC template",
        "strategy_parameters": "frozen; signals regenerated on every permuted path",
        "warning": "expensive: every iteration runs the complete backtest",
        "source": str(source), "source_sha256": source_hash,
        "spec": str(spec_path), "spec_sha256": spec_hash,
        "seed": args.seed, "requested_runs": args.runs,
        "completed_runs": completed, "failed_runs": failed,
        "workers": args.workers, "elapsed_seconds": time.perf_counter() - started,
        "runs": ordered, "p_values_reported": False,
    })
    if failed:
        raise SystemExit(f"{failed} bar-permutation runs failed; no p-value was produced")


if __name__ == "__main__":
    main()
