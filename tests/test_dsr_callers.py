"""DSR benchmark arguments must not inherit the displayed t-statistics."""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import benchmark

os.environ.setdefault("BT_CSV", str(ROOT / "data" / "SOLUSDT_1h.csv"))
from backtester import dsr


def test_benchmark_trials_use_own_return_counts():
    returns = {
        ("SOL", "a"): np.array([-.02, .01, .03, -.01]),
        ("SOL", "b"): np.array([.01, -.02, .03, -.01, .02, .01, -.03, .04]),
        ("BTC", "a"): np.array([.01, .03, .02, .04]),
    }
    rows = [{"dataset": ds, "strategy": sid, "net_sharpe": 99.0, "dsr": None}
            for ds, sid in returns]
    benchmark._fill_dsr(rows, returns)
    trials = [dsr.sharpe_per_observation(returns[("SOL", sid)]) for sid in ["a", "b"]]
    for row in rows[:2]:
        rets = returns[(row["dataset"], row["strategy"])]
        expected = dsr.deflated_sharpe_ratio(dsr.sharpe_per_observation(rets), trials, rets)
        assert row["dsr"] == pytest.approx(expected)
        assert row["net_sharpe"] == 99.0
    assert rows[2]["dsr"] is None  # One dataset's trials never enter another's.
