#!/usr/bin/env python3
"""Cross-engine checks for expanding-window WFO on normal, regime and FX paths."""

import os
import sys
import tempfile
import math

import parity_common as pc
import parity_forex
import parity_regime
import parity_ledger

EXPECTED_TAGS = [f"W{window:02d} {sample}" for window in range(1, 4) for sample in ("IS", "OOS")]
FIELDS = ["trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"]


def _require_surface(metrics, engine):
    for tag in EXPECTED_TAGS:
        if tag not in metrics:
            raise AssertionError(f"{engine} output is missing {tag}")
        for field in FIELDS:
            if field not in metrics[tag]:
                raise AssertionError(f"{engine} output is missing {tag}.{field}")


def _compare_oos_ledgers(py_path, rs_path, tolerance=1e-9):
    py_all = parity_ledger.load_ledger(py_path)
    rs_all = parity_ledger.load_ledger(rs_path)
    if not any(row.window.startswith("LB") for row in rs_all):
        raise AssertionError("Rust ledger lost its classic baseline rows")
    py = [row for row in py_all
          if row.window in {"W01", "W02", "W03"} and row.sample == "OOS"]
    rs = [row for row in rs_all
          if row.window in {"W01", "W02", "W03"} and row.sample == "OOS"]
    if not py or len(py) != len(rs):
        raise AssertionError(f"OOS ledger row count differs: Python={len(py)}, Rust={len(rs)}")
    for window in ("W01", "W02", "W03"):
        if not any(row.window == window for row in py):
            raise AssertionError(f"Python ledger has no {window} OOS rows")
        if not any(row.window == window for row in rs):
            raise AssertionError(f"Rust ledger has no {window} OOS rows")
    exact = ("window", "side", "entry_unix", "exit_unix")
    numeric = ("open_entry", "high_entry", "low_entry", "close_entry",
               "open_exit", "high_exit", "low_exit", "close_exit", "pnl")
    for index, (left, right) in enumerate(zip(py, rs)):
        for field in exact:
            if getattr(left, field) != getattr(right, field):
                raise AssertionError(f"ledger row {index} differs at {field}")
        for field in numeric:
            a, b = getattr(left, field), getattr(right, field)
            if not math.isfinite(a) or not math.isfinite(b):
                raise AssertionError(f"ledger row {index} has non-finite {field}: {a} vs {b}")
            if abs(a - b) > tolerance * max(1.0, abs(a), abs(b)):
                raise AssertionError(f"ledger row {index} differs at {field}: {a} vs {b}")
    return len(py)


def _metric_mismatch_count(left, right, tolerance=0.001):
    mismatches = 0
    for tag in EXPECTED_TAGS:
        for field in FIELDS:
            a, b = left[tag][field], right[tag][field]
            if field == "trades":
                mismatches += a != b
            else:
                scale = max(abs(a), abs(b), 1e-12)
                mismatches += abs(a - b) / scale > tolerance
    return mismatches


def run_case(name, csv, python_overrides, rust_runner, python_setup=""):
    print(f"\n{name}: {csv}")
    overrides = {
        "OOS_CANDLES": "12500",
        "WFO_WINDOW_MODE": repr("expanding"),
        **python_overrides,
    }
    with tempfile.TemporaryDirectory(prefix=f"qrf-expanding-{name}-") as directory:
        directory = pc.Path(directory)
        py_ledger = directory / "python.csv"
        rs_ledger = directory / "rust.csv"
        os.environ["BT_EXPORT_PATH"] = str(py_ledger)
        py = pc.parse_metrics(pc.run_python(csv, overrides=overrides, extra_setup=python_setup))
        os.environ["BT_EXPORT_PATH"] = str(rs_ledger)
        rs = pc.parse_metrics(rust_runner(csv))
        _require_surface(py, "Python")
        _require_surface(rs, "Rust")
        failures = pc.compare(py, rs, EXPECTED_TAGS, FIELDS, 0.001)
        rows = _compare_oos_ledgers(py_ledger, rs_ledger)
        print(f"  compared {len(EXPECTED_TAGS) * len(FIELDS)} metrics and {rows} OOS ledger rows")
        return failures, py, rs


def main():
    previous = {key: os.environ.get(key) for key in ("BT_WFO_WINDOW_MODE", "BT_OOS_CANDLES", "BT_EXPORT_PATH")}
    os.environ["BT_WFO_WINDOW_MODE"] = "expanding"
    os.environ["BT_OOS_CANDLES"] = "12500"
    try:
        sol = pc.REPO_RUST / "data" / "SOLUSDT_1h.csv"
        eurusd = pc.REPO_RUST / "data" / "EURUSD_1h.csv"
        failures = 0
        failed, normal_py, _ = run_case(
            "normal",
            sol,
            {},
            pc.run_rust_default_binary,
        )
        failures += failed
        failed, _, _ = run_case(
            "regime",
            sol,
            {"USE_WFO": "True", "USE_REGIME_SEG": "True"},
            lambda csv: pc.run_rust_example(
                "_parity_regime",
                pc.REPO_RUST / "examples" / "_parity_regime.rs",
                parity_regime.RUST_DRIVER,
                csv,
            ),
        )
        failures += failed
        failed, _, _ = run_case(
            "forex",
            eurusd,
            {},
            lambda csv: pc.run_rust_example(
                "_parity_forex",
                pc.REPO_RUST / "examples" / "_parity_forex.rs",
                parity_forex.RUST_DRIVER,
                csv,
            ),
            python_setup=parity_forex.PY_FOREX_SETUP,
        )
        failures += failed

        os.environ["BT_WFO_WINDOW_MODE"] = "rolling"
        with tempfile.TemporaryDirectory(prefix="qrf-expanding-negative-") as directory:
            os.environ["BT_EXPORT_PATH"] = str(pc.Path(directory) / "rolling-rust.csv")
            rolling_rs = pc.parse_metrics(pc.run_rust_default_binary(sol))
        _require_surface(rolling_rs, "Rust rolling negative control")
        negative_mismatches = _metric_mismatch_count(normal_py, rolling_rs)
        if negative_mismatches == 0:
            raise AssertionError("negative control did not distinguish rolling from expanding")
        print(f"\nnegative control: {negative_mismatches} metric mismatches at 0.1% tolerance")
        return 0 if failures == 0 else 1
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    sys.exit(main())
