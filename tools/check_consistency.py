#!/usr/bin/env python3
"""Cross-artifact consistency guard.

Fails (exit 1) if the license, version, or headline-figure claims drift across
the repo's artifacts. Wired into CI (parity.yml) so the framework's correctness
*claims* are enforced continuously, not asserted once and left to rot.

Checks BOTH engines: the Rust repo this file lives in, and the Python sibling
(via BT_PY_REPO, else ../quant-research-framework). The paper (.tex, not in the
repo) is reconciled separately and is out of CI scope.

    python tools/check_consistency.py        # exit 0 = consistent, 1 = drift
"""
import os
import re
import sys
from pathlib import Path

REPO_RS = Path(__file__).resolve().parent.parent
REPO_PY = Path(os.environ.get("BT_PY_REPO", REPO_RS.parent / "quant-research-framework"))

CANON_LICENSE = "Apache-2.0"
fails: list[str] = []


def rd(p) -> str | None:
    p = Path(p)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def must(cond, msg):
    if not cond:
        fails.append(msg)


# ---- 1. LICENSE bodies are Apache-2.0, not MIT ----
for name, repo in (("rust", REPO_RS), ("python", REPO_PY)):
    t = rd(repo / "LICENSE")
    must(t is not None, f"[license] {name}: LICENSE file missing")
    if t:
        must("Apache License" in t, f"[license] {name}: LICENSE body is not Apache-2.0")
        must("MIT License" not in t, f"[license] {name}: LICENSE still contains 'MIT License'")

# ---- 2. manifest license fields ----
cargo = rd(REPO_RS / "Cargo.toml") or ""
m = re.search(r'(?m)^\s*license\s*=\s*"([^"]+)"', cargo)
must(bool(m) and m.group(1) == CANON_LICENSE,
     f"[license] Cargo.toml license != {CANON_LICENSE} (got {m.group(1) if m else 'none'})")
pyproj = rd(REPO_PY / "pyproject.toml") or ""
must("Apache Software License" in pyproj, "[license] pyproject classifier not Apache Software License")
must("OSI Approved :: MIT" not in pyproj, "[license] pyproject still carries an MIT classifier")

# ---- 3. READMEs carry no stray MIT in a license context ----
for name, repo in (("rust", REPO_RS), ("python", REPO_PY)):
    r = rd(repo / "README.md") or ""
    must("License-MIT" not in r, f"[license] {name} README still shows an MIT license badge")
    for line in r.splitlines():
        if "**this**" in line and "(Python" in line:  # the self-row of the comparison matrix
            must("MIT" not in line and "Apache" in line,
                 f"[license] {name} README comparison self-row not Apache: {line.strip()[:70]}")
        if re.search(r'(?i)^\s*(license[: ]+)?MIT\b.*\bLICENSE\b', line):  # "MIT — see LICENSE" footer
            fails.append(f"[license] {name} README footer still says MIT: {line.strip()[:70]}")

# ---- 4. version synced across Cargo / pyproject / __version__ / both CHANGELOG tops ----
def first(pat, txt, g=1):
    mm = re.search(pat, txt or "")
    return mm.group(g) if mm else None

cit_rs, cit_py = rd(REPO_RS / "CITATION.cff") or "", rd(REPO_PY / "CITATION.cff") or ""
versions = {
    "Cargo.toml":   first(r'(?m)^\s*version\s*=\s*"([^"]+)"', cargo),
    "pyproject":    first(r'(?m)^\s*version\s*=\s*"([^"]+)"', pyproj),
    "__version__":  first(r'__version__\s*=\s*"([^"]+)"', rd(REPO_PY / "backtester" / "__init__.py")),
    "CHANGELOG-rs": first(r'(?m)^##\s*\[([0-9][^\]]*)\]', rd(REPO_RS / "CHANGELOG.md")),
    "CHANGELOG-py": first(r'(?m)^##\s*\[([0-9][^\]]*)\]', rd(REPO_PY / "CHANGELOG.md")),
    "CITATION-rs":  first(r'(?m)^version:\s*"?([0-9][^"\s]*)"?', cit_rs),
    "CITATION-py":  first(r'(?m)^version:\s*"?([0-9][^"\s]*)"?', cit_py),
}
distinct = {v for v in versions.values() if v}
must(len(distinct) == 1, f"[version] not synchronised: {versions}")

# ---- 4b. CITATION.cff license fields are Apache-2.0, not MIT ----
for name, cit in (("rust", cit_rs), ("python", cit_py)):
    must(bool(cit), f"[cite] {name}: CITATION.cff missing")
    if cit:
        must("license: MIT" not in cit, f"[cite] {name} CITATION.cff still declares 'license: MIT'")
        must(f"license: {CANON_LICENSE}" in cit,
             f"[cite] {name} CITATION.cff does not declare license: {CANON_LICENSE}")

# ---- 5. one speed band, identical across both READMEs ----
def speed_band(txt):
    mm = re.search(r'(\d+(?:\.\d+)?)\s*[–-]\s*(\d+(?:\.\d+)?)\s*×\s*(?:\*\*\s*)?faster', txt or "")
    return f"{mm.group(1)}-{mm.group(2)}" if mm else None

b_rs, b_py = speed_band(rd(REPO_RS / "README.md")), speed_band(rd(REPO_PY / "README.md"))
must(b_rs is not None and b_rs == b_py,
     f"[speed] README speed-up bands differ or missing: rust={b_rs} python={b_py}")

# ---- report ----
if fails:
    print("CONSISTENCY GUARD: FAIL\n")
    for f in fails:
        print("  ✗", f)
    print(f"\n{len(fails)} inconsistency(ies).")
    sys.exit(1)
print("CONSISTENCY GUARD: OK — license / version / speed claims consistent across all repo artifacts.")
