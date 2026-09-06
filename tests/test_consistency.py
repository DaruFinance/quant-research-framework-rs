"""The documentation guard must reject independently introduced claim drift."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.fixture
def artifact_pair(tmp_path):
    source_rs = Path(__file__).resolve().parents[1]
    source_py = Path(os.environ.get("QRF_PY_DIR", source_rs.parent / "quant-research-framework"))
    rs, py = tmp_path / "rust", tmp_path / "python"
    for source, target in ((source_rs, rs), (source_py, py)):
        for name in ("LICENSE", "README.md", "CHANGELOG.md", "CITATION.cff"):
            target.mkdir(exist_ok=True)
            shutil.copy2(source / name, target / name)
    for source, target, names in (
        (source_rs, rs, ("Cargo.toml", "tools/check_consistency.py")),
        (source_py, py, ("pyproject.toml", "docs/conf.py", "backtester/__init__.py")),
    ):
        for name in names:
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target / name)
    shutil.copytree(source_rs / "data/golden", rs / "data/golden")
    return rs, py


def run_guard(pair):
    rs, py = pair
    return subprocess.run(
        [sys.executable, str(rs / "tools/check_consistency.py")],
        env={**os.environ, "QRF_PY_DIR": str(py)},
        capture_output=True, text=True,
    )


def test_current_artifacts_agree(artifact_pair):
    result = run_guard(artifact_pair)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("repo_index", [0, 1])
def test_readme_metric_drift_fails(artifact_pair, repo_index):
    readme = artifact_pair[repo_index] / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "194 metric lines each, 1,164", "196 metric lines each, 1,176"), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[golden]" in result.stdout


def test_snapshot_drift_fails(artifact_pair):
    golden = next((artifact_pair[0] / "data/golden").glob("*.x86_64.txt"))
    golden.write_text(golden.read_text(encoding="utf-8") + "extra metric\n", encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[golden]" in result.stdout


def test_stale_sphinx_version_fails(artifact_pair):
    config = artifact_pair[1] / "docs/conf.py"
    config.write_text('release = "0.4.0"\n', encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "Sphinx release differs" in result.stdout


def test_stale_readme_version_fails(artifact_pair):
    readme = artifact_pair[1] / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        "currently `0.7.6`", "currently `0.6.0`"), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "README current version differs" in result.stdout
