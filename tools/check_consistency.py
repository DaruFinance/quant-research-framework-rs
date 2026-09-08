#!/usr/bin/env python3
"""Cross-artifact consistency guard.

Fails (exit 1) if the license, version, or headline-figure claims drift across
the repo's artifacts. Wired into CI (parity.yml) so the framework's correctness
*claims* are enforced continuously, not asserted once and left to rot.

Checks BOTH engines: the Rust repo this file lives in, and the Python sibling
(via QRF_PY_DIR, the variable the parity harnesses use, with BT_PY_REPO still
honoured for compatibility, else ../quant-research-framework). The paper (.tex,
not in the repo) is reconciled separately and is out of CI scope.

    python tools/check_consistency.py        # exit 0 = consistent, 1 = drift
"""
import json
import os
import re
import runpy
import sys
from pathlib import Path

REPO_RS = Path(__file__).resolve().parent.parent
REPO_PY = Path(
    os.environ.get("QRF_PY_DIR")
    or os.environ.get("BT_PY_REPO")
    or REPO_RS.parent / "quant-research-framework"
)

CANON_LICENSE = "Apache-2.0"
CANON_PUBLIC_AUTHOR = "Daniel Gatto"
CANON_CITATION_FAMILY = "Gatto"
CANON_CITATION_GIVEN = "Daniel V."
CANON_ZENODO_CREATOR = "Gatto, Daniel V."
CANON_PUBLIC_EMAIL = "daniel@daru.finance"
fails: list[str] = []


def rd(p) -> str | None:
    p = Path(p)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def must(cond, msg):
    if not cond:
        fails.append(msg)


def top_level_yaml_block(text, key):
    match = re.search(
        rf"(?ms)^{re.escape(key)}:\s*\n(.*?)(?=^[A-Za-z][A-Za-z0-9_-]*:\s*|\Z)",
        text,
    )
    return match.group(1) if match else ""


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
        if re.search(r'(?i)^\s*(license[: ]+)?MIT\b.*\bLICENSE\b', line):  # "MIT, see LICENSE" footer
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
must(all(versions.values()), f"[version] missing version fields: {versions}")
readme_version = first(r'currently `([^`]+)`', rd(REPO_PY / "README.md"))
must(readme_version == versions["pyproject"],
     f"[version] Python README current version differs: {readme_version}")
try:
    docs_version = runpy.run_path(str(REPO_PY / "docs" / "conf.py"))["release"]
except (OSError, KeyError, AttributeError) as exc:
    docs_version = None
    fails.append(f"[version] cannot read Sphinx release: {exc}")
must(docs_version == versions["pyproject"],
     f"[version] Sphinx release differs: {docs_version}")

# ---- 4b. CITATION.cff license fields are Apache-2.0, not MIT ----
for name, cit in (("rust", cit_rs), ("python", cit_py)):
    must(bool(cit), f"[cite] {name}: CITATION.cff missing")
    if cit:
        must("license: MIT" not in cit, f"[cite] {name} CITATION.cff still declares 'license: MIT'")
        must(f"license: {CANON_LICENSE}" in cit,
             f"[cite] {name} CITATION.cff does not declare license: {CANON_LICENSE}")

# ---- 4c. public author and contact metadata ----
email_re = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
for name, repo, cit in (("rust", REPO_RS, cit_rs), ("python", REPO_PY, cit_py)):
    authors = top_level_yaml_block(cit, "authors")
    family_pattern = rf'(?m)^\s*-\s+family-names:\s*"{re.escape(CANON_CITATION_FAMILY)}"\s*$'
    given_pattern = rf'(?m)^\s+given-names:\s*"{re.escape(CANON_CITATION_GIVEN)}"\s*$'
    must(bool(re.search(family_pattern, authors)),
         f"[author] {name} CITATION.cff family name is not {CANON_CITATION_FAMILY!r}")
    must(bool(re.search(given_pattern, authors)),
         f"[author] {name} CITATION.cff given names are not {CANON_CITATION_GIVEN!r}")
    must(set(email_re.findall(authors)) == {CANON_PUBLIC_EMAIL},
         f"[author] {name} CITATION.cff contact is not the public address")
    try:
        zenodo = json.loads((repo / ".zenodo.json").read_text(encoding="utf-8"))
        creator = zenodo["creators"][0]["name"]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        creator = None
        fails.append(f"[author] {name} .zenodo.json cannot be read: {exc}")
    must(creator == CANON_ZENODO_CREATOR,
         f"[author] {name} Zenodo creator differs: {creator!r}")

py_author = re.search(
    r'(?m)^authors\s*=\s*\[\{\s*name\s*=\s*"([^"]+)",\s*email\s*=\s*"([^"]+)"\s*\}\]\s*$',
    pyproj,
)
must(bool(py_author), "[author] pyproject author record is missing")
if py_author:
    must(py_author.group(1) == CANON_PUBLIC_AUTHOR,
         f"[author] pyproject author differs: {py_author.group(1)!r}")
    must(py_author.group(2) == CANON_PUBLIC_EMAIL,
         "[author] pyproject contact is not the public address")

for label, manifest in (
    ("Cargo.toml", cargo),
    ("vendored Cargo.toml", rd(REPO_PY / "mc_bar_permutation" / "rust" / "vendor"
                               / "quant-research-framework-rs" / "Cargo.toml") or ""),
):
    cargo_author = re.search(r'(?m)^authors\s*=\s*\["([^"]+)"\]\s*$', manifest)
    expected = f"{CANON_PUBLIC_AUTHOR} <{CANON_PUBLIC_EMAIL}>"
    must(bool(cargo_author) and cargo_author.group(1) == expected,
         f"[author] {label} author record differs")
    must(set(email_re.findall(manifest)) <= {CANON_PUBLIC_EMAIL},
         f"[author] {label} contains a non-public contact address")

# ---- 5. measured results and README headline agree across both repos ----
def benchmark_result(repo):
    path = repo / "benchmarks" / "2026-09-07-results.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fails.append(f"[speed] cannot read {path}: {exc}")
        return None


result_rs, result_py = benchmark_result(REPO_RS), benchmark_result(REPO_PY)
must(result_rs is not None and result_rs == result_py,
     "[speed] machine-readable benchmark results differ across repositories")
if result_rs:
    ratio = result_rs["wfo"]["python_over_rust_wall_ratio"]
    expected = f"{ratio:.2f} times less wall time"
    for name, repo in (("rust", REPO_RS), ("python", REPO_PY)):
        must(expected in (rd(repo / "README.md") or ""),
             f"[speed] {name} README does not report {expected!r}")

# ---- 6. README metric totals come from the committed golden files ----
goldens = sorted((REPO_RS / "data" / "golden").glob("*.x86_64.txt"))
counts = [len(p.read_text(encoding="utf-8").splitlines()) for p in goldens]
must(len(counts) == 6 and len(set(counts)) == 1,
     f"[golden] expected six equal-sized metric snapshots, got {counts}")
for name, repo in (("rust", REPO_RS), ("python", REPO_PY)):
    claim = re.search(r'\((\d+) metric lines each, ([\d,]+) in total\)',
                      rd(repo / "README.md") or "")
    actual = (counts[0], sum(counts)) if counts else None
    stated = (int(claim[1]), int(claim[2].replace(",", ""))) if claim else None
    must(stated is not None and stated == actual,
         f"[golden] {name} README totals differ: stated={stated}, actual={actual}")

# ---- report ----
if fails:
    print("CONSISTENCY GUARD: FAIL\n")
    for f in fails:
        print("  ✗", f)
    print(f"\n{len(fails)} inconsistency(ies).")
    sys.exit(1)
print("CONSISTENCY GUARD: OK, license / version / author / speed / golden counts consistent across repo artifacts.")
