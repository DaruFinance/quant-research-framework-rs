# Releasing

Release process for the Rust port. The Python framework has its own
`RELEASING.md`; the two coordinate via matching version-suffix tags.

## One-time crates.io setup

1. On `crates.io` (logged in as the publishing account), generate an API
   token at *Account Settings → API Tokens*.
2. In this GitHub repo: *Settings → Secrets and variables → Actions →
   New repository secret*. Name `CRATES_IO_TOKEN`, value the token from
   step 1.
3. The `.github/workflows/publish-crates.yml` workflow will run on every
   `v*` tag push and publish to crates.io using that token.

## Cutting a release

```bash
# 1. Bump the version in Cargo.toml and CITATION.cff.
$EDITOR Cargo.toml CITATION.cff

# 2. CHANGELOG.md entry (Keep-A-Changelog format).
$EDITOR CHANGELOG.md

# 3. Build + test locally.
cargo build --release
cargo test --release
cargo clippy --release --no-deps -- -D warnings
cargo fmt --check

# 4. Verify cross-language parity hasn't regressed.
python tools/parity_check.py --csv data/SOLUSDT_1h.csv --tol 0.001
python tools/parity_regime.py --csv data/SOLUSDT_1h.csv --tol 0.001
python tools/parity_forex.py --csv data/EURUSD_1h.csv --tol 0.001
python tools/parity_ledger.py --csv data/SOLUSDT_1h.csv --tol 0.001

# 5. Commit, tag, push.
git add Cargo.toml Cargo.lock CITATION.cff CHANGELOG.md
git commit -m "release: vX.Y.Z"
git tag vX.Y.Z
git push origin main vX.Y.Z

# 6. publish-crates.yml will trigger automatically. Verify on
#    https://crates.io/crates/quant-research-framework-rs
```

## Coordinating with the Python reference

If the release changes engine semantics (anything that affects the
metric output of the cross-language parity surfaces), the Python
reference must land the matching change in the same release window.

| Rust tag | Python tag | Notes |
|---|---|---|
| `v0.3.2` | `v0.3.0` | paper-v2 |
| `v0.3.3` | `v0.3.1` | paper-v2 polish |
