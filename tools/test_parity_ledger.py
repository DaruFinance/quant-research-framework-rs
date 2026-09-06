"""Harness isolation regression checks; subprocess calls are mocked."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import parity_ledger as harness


def test_runners_pass_the_requested_export_path(tmp_path):
    output = tmp_path / "trades.csv"
    with patch.object(harness.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run:
        for runner in (harness.run_python, harness.run_rust, harness.run_rust_forex):
            assert runner(tmp_path / "input.csv", output) == output
            assert run.call_args.kwargs["env"]["BT_EXPORT_PATH"] == str(output.resolve())


def test_invocations_use_distinct_private_directories(tmp_path):
    csv = tmp_path / "input.csv"
    csv.touch()
    paths = []

    def runner(_csv, output, **_kwargs):
        paths.append(output)
        return output

    with patch.object(harness.sys, "argv", ["parity_ledger.py", "--csv", str(csv)]), \
            patch.object(harness, "REPO_PY", tmp_path), \
            patch.object(harness, "run_python", side_effect=runner), \
            patch.object(harness, "run_rust", side_effect=runner), \
            patch.object(harness, "load_ledger", return_value=[]), \
            patch.object(harness, "compare", return_value=0):
        assert harness.main() == 0
        assert harness.main() == 0
    assert len(set(paths)) == 4
    assert paths[0].parent.parent == paths[1].parent.parent
    assert paths[0].parent.parent != paths[2].parent.parent
    assert all(not path.parent.parent.exists() for path in paths)
