import csv
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import parity_expanding_wfo as gate


def complete_metrics(value=1.0):
    return {
        tag: {field: (1 if field == "trades" else value) for field in gate.FIELDS}
        for tag in gate.EXPECTED_TAGS
    }


class ExpandingGateTests(unittest.TestCase):
    def test_missing_tag_and_field_fail(self):
        metrics = complete_metrics()
        metrics.pop("W03 OOS")
        with self.assertRaisesRegex(AssertionError, "W03 OOS"):
            gate._require_surface(metrics, "test")

        metrics = complete_metrics()
        metrics["W02 IS"].pop("sharpe")
        with self.assertRaisesRegex(AssertionError, "W02 IS.sharpe"):
            gate._require_surface(metrics, "test")

    def test_negative_control_uses_published_tolerance(self):
        left = complete_metrics(1.0)
        within = complete_metrics(1.0005)
        outside = complete_metrics(1.002)
        self.assertEqual(gate._metric_mismatch_count(left, within), 0)
        self.assertGreater(gate._metric_mismatch_count(left, outside), 0)

    def test_nonfinite_ledger_fails(self):
        columns = [
            "strategy", "window", "sample", "side", "entry_time",
            "open_entry", "high_entry", "low_entry", "close_entry", "exit_time",
            "open_exit", "high_exit", "low_exit", "close_exit", "pnl",
        ]
        row = {
            "strategy": "test", "window": "LB50", "sample": "IS-opt", "side": "1",
            "entry_time": "1", "exit_time": "2", "open_entry": "1",
            "high_entry": "1", "low_entry": "1", "close_entry": "1",
            "open_exit": "1", "high_exit": "1", "low_exit": "1",
            "close_exit": "1", "pnl": "1",
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("a.csv", "b.csv")]
            for path in paths:
                with path.open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=columns)
                    writer.writeheader()
                    writer.writerow(row)
                    for window in ("W01", "W02", "W03"):
                        oos = dict(row, window=window, sample="OOS")
                        if window == "W01":
                            oos["open_entry"] = "nan"
                        writer.writerow(oos)
            with self.assertRaisesRegex(AssertionError, "non-finite"):
                gate._compare_oos_ledgers(*paths)


if __name__ == "__main__":
    unittest.main()
