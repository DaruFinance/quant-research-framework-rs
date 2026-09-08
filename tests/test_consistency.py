"""The documentation guard must reject independently introduced claim drift."""
import json
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
        for name in ("LICENSE", "README.md", "CHANGELOG.md", "CITATION.cff", ".zenodo.json"):
            target.mkdir(exist_ok=True)
            shutil.copy2(source / name, target / name)
    for source, target, names in (
        (source_rs, rs, ("Cargo.toml", "tools/check_consistency.py")),
        (source_py, py, ("pyproject.toml", "docs/conf.py", "backtester/__init__.py")),
    ):
        for name in names:
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / name, target / name)
    vendored_cargo = Path("mc_bar_permutation/rust/vendor/quant-research-framework-rs/Cargo.toml")
    (py / vendored_cargo).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_py / vendored_cargo, py / vendored_cargo)
    shutil.copytree(source_rs / "data/golden", rs / "data/golden")
    for source, target in ((source_rs, rs), (source_py, py)):
        benchmark = target / "benchmarks" / "2026-09-07-results.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "benchmarks" / benchmark.name, benchmark)
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


def test_benchmark_result_drift_fails(artifact_pair):
    benchmark = artifact_pair[1] / "benchmarks" / "2026-09-07-results.json"
    result_data = json.loads(benchmark.read_text(encoding="utf-8"))
    result_data["wfo"]["python_over_rust_wall_ratio"] = 99.0
    benchmark.write_text(json.dumps(result_data), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[speed]" in result.stdout


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


@pytest.mark.parametrize("repo_index", [0, 1])
def test_citation_author_drift_fails(artifact_pair, repo_index):
    citation = artifact_pair[repo_index] / "CITATION.cff"
    citation.write_text(citation.read_text(encoding="utf-8").replace(
        'given-names: "Daniel V."', 'given-names: "Wrong Name"'), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[author]" in result.stdout


def test_public_contact_missing_fails(artifact_pair):
    pyproject = artifact_pair[1] / "pyproject.toml"
    pyproject.write_text(pyproject.read_text(encoding="utf-8").replace(
        'email = "daniel@daru.finance"', 'email = ""'), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[author]" in result.stdout


@pytest.mark.parametrize("repo_index", [0, 1])
def test_citation_contact_drift_fails(artifact_pair, repo_index):
    citation = artifact_pair[repo_index] / "CITATION.cff"
    citation.write_text(citation.read_text(encoding="utf-8").replace(
        "daniel@daru.finance", "contact@example.invalid"), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[author]" in result.stdout


@pytest.mark.parametrize("repo_index,relative_path", [
    (0, Path("Cargo.toml")),
    (1, Path("mc_bar_permutation/rust/vendor/quant-research-framework-rs/Cargo.toml")),
])
def test_cargo_author_contact_drift_fails(artifact_pair, repo_index, relative_path):
    manifest = artifact_pair[repo_index] / relative_path
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        "daniel@daru.finance", "contact@example.invalid"), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[author]" in result.stdout


@pytest.mark.parametrize("repo_index", [0, 1])
def test_zenodo_creator_drift_fails(artifact_pair, repo_index):
    zenodo_path = artifact_pair[repo_index] / ".zenodo.json"
    zenodo = json.loads(zenodo_path.read_text(encoding="utf-8"))
    zenodo["creators"][0]["name"] = "Wrong Name"
    zenodo_path.write_text(json.dumps(zenodo), encoding="utf-8")
    result = run_guard(artifact_pair)
    assert result.returncode == 1 and "[author]" in result.stdout
