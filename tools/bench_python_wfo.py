#!/usr/bin/env python3
"""Python side of the 150k/10-configuration WFO benchmark."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

for variable in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "NUMBA_NUM_THREADS",
):
    os.environ[variable] = "1"

import numpy as np


def load_strategy_library(python_repo: Path):
    path = python_repo / "examples" / "batch_runner" / "run_batch.py"
    spec = importlib.util.spec_from_file_location("qrf_batch_library", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixed(backtester, function, lookback):
    def signal(frame, _candidate_lookback):
        backtester._runtime_state["_last_df"] = frame
        backtester._runtime_state["_last_lb"] = lookback
        return function(frame, lookback)

    return signal


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metrics(returns: np.ndarray) -> dict:
    returns = np.asarray(returns, dtype=float)
    wins = returns[returns > 0]
    losses = -returns[returns <= 0]
    equity = np.concatenate(([1.0], 1.0 + np.cumsum(returns)))
    high = np.maximum.accumulate(equity)
    return {
        "trades": int(returns.size),
        "roi": float(returns.sum()) if returns.size else 0.0,
        "pf": float(wins.sum() / losses.sum()) if losses.size else None,
        "sharpe": float(returns.mean() / returns.std() * math.sqrt(returns.size))
        if returns.size > 1 and returns.std()
        else 0.0,
        "max_drawdown": float(np.max((high - equity) / high)) if returns.size else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-repo", type=Path, required=True)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    sys.path.insert(0, str(args.python_repo))
    import backtester as bt

    library = load_strategy_library(args.python_repo)
    specs = [
        ("ema_cross_lb14_tp2.0", "ema_cross", library.signal_ema_cross, 14, 2.0),
        ("ema_cross_lb40_tp4.5", "ema_cross", library.signal_ema_cross, 40, 4.5),
        ("atr_cross_lb20_tp1.6", "atr_cross", library.signal_atr_cross, 20, 1.6),
        ("atr_cross_lb50_tp2.0", "atr_cross", library.signal_atr_cross, 50, 2.0),
        ("macd_zero_lb12_tp3.0", "macd_zero", library.signal_macd_zero, 12, 3.0),
        ("macd_zero_lb26_tp3.0", "macd_zero", library.signal_macd_zero, 26, 3.0),
        ("rsi_revert_lb14_tp0.5", "rsi_revert", library.signal_rsi_revert, 14, 0.5),
        ("rsi_revert_lb28_tp2.0", "rsi_revert", library.signal_rsi_revert, 28, 2.0),
        ("stoch_kd_lb14_tp0.8", "stoch_kd", library.signal_stoch_kd, 14, 0.8),
        ("stoch_kd_lb21_tp1.2", "stoch_kd", library.signal_stoch_kd, 21, 1.2),
    ]

    args.out.mkdir(parents=True, exist_ok=False)
    ledger_dir = args.out / "ledgers"
    ledger_dir.mkdir()
    process_start = time.perf_counter_ns()
    frame = bt.load_ohlc(str(args.csv))
    assert len(frame) == 150_000
    bt.ROBUSTNESS_SCENARIOS = {}
    results = []

    for name, family, function, lookback, take_profit in specs:
        ledger = ledger_dir / f"{name}.csv"
        config = bt.Config(
            csv_file=str(args.csv),
            export_path=str(ledger),
            backtest_candles=10_000,
            oos_candles=140_000,
            default_lb=50,
            lookback_range=(12, 76),
            sl_percentage=1.0,
            tp_percentage=take_profit,
            use_sl=True,
            use_tp=True,
            optimize_rrr=True,
            use_wfo=True,
            wfo_trigger_mode="candles",
            wfo_trigger_val=5_000,
            use_monte_carlo=False,
            print_equity_curve=False,
            fee_pct=0.02,
            slippage_pct=0.03,
            funding_fee=0.01,
            fee_shock=False,
            slippage_shock=False,
            news_candles_injection=False,
            entry_drift=False,
            indicator_variance=False,
        )
        assert not any(
            (
                config.use_monte_carlo,
                config.fee_shock,
                config.slippage_shock,
                config.news_candles_injection,
                config.entry_drift,
                config.indicator_variance,
            )
        )
        assert (
            config.backtest_candles,
            config.oos_candles,
            config.wfo_trigger_val,
        ) == (10_000, 140_000, 5_000)
        assert (
            config.sl_percentage,
            config.fee_pct,
            config.slippage_pct,
            config.funding_fee,
        ) == (1.0, 0.02, 0.03, 0.01)
        bt.create_raw_signals = fixed(bt, function, lookback)
        bt.signals_cache.clear()
        bt._runtime_state["last_unfiltered_raw"] = None
        started = time.perf_counter_ns()
        returns, _equity, _robustness, _split = bt.walk_forward(
            frame, {}, np.array([1.0]), config=config
        )
        elapsed = (time.perf_counter_ns() - started) / 1e9
        results.append(
            {
                "name": name,
                "family": family,
                "fixed_lb": lookback,
                "tp_input_pct": take_profit,
                "windows": 28,
                "elapsed_s": elapsed,
                "ledger_rows": sum(1 for _ in ledger.open(encoding="utf-8")) - 1,
                "ledger_sha256": sha256(ledger),
                "metrics": metrics(returns),
            }
        )

    output = {
        "engine": "qrf-python",
        "processes": 1,
        "strategies": 10,
        "bars": 150_000,
        "is_bars": 10_000,
        "oos_bars": 140_000,
        "expected_windows_each": 28,
        "boundary": "fresh process; one CSV load; serial WFO-only configurations; no classic, Monte Carlo, robustness, or warm-up",
        "fixed_lb_note": "each wrapper ignores the engine candidate LB and evaluates its named fixed LB; the unchanged candidate search is redundant timed work",
        "rrr_note": "dynamic RRR optimization remains enabled, so tp_input_pct is a traceability label rather than the selected OOS take-profit",
        "process_internal_wall_s": (time.perf_counter_ns() - process_start) / 1e9,
        "results": results,
    }
    (args.out / "results.json").write_text(json.dumps(output, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
