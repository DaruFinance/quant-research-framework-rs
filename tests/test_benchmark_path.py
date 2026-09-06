"""The benchmark must select the same reference checkout as parity tools."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("modern,legacy,expected", [
    ("modern-checkout", "legacy-checkout", "modern-checkout"),
    (None, "legacy-checkout", "legacy-checkout"),
    ("", "legacy-checkout", "legacy-checkout"),
    (None, None, "quant-research-framework"),
])
def test_reference_checkout_precedence(modern, legacy, expected):
    repo = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in {"QRF_PY_DIR", "BT_PY_REPO"}}
    for name, value in (("QRF_PY_DIR", modern), ("BT_PY_REPO", legacy)):
        if value is not None:
            env[name] = value
    code = (
        "import runpy, json; "
        "m = runpy.run_path('tools/benchmark.py'); "
        "print(json.dumps(str(m['_PY_REPO'])))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=repo,
                            env=env, capture_output=True, text=True, check=True)
    assert Path(json.loads(result.stdout)).name == expected
