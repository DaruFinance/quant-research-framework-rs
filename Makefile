# Convenience targets for the Rust port.
#
#   make repro  : the <5-minute reproduction: build, cross-language parity
#                  suite, and the no-look-ahead leak guard. This is what the
#                  README and CI run.
#   make parity : just the three published parity surfaces
#   make leak   : just the look-ahead leak demo
#   make bench  : the frozen robustness benchmark, golden drift-guarded
#   make test   : cargo test (behavioural + invariant)
#
# The parity/leak targets drive BOTH engines, so they need the Python
# reference checkout. It defaults to a sibling directory; override with:
#   make repro QRF_PY_DIR=/path/to/quant-research-framework
QRF_PY_DIR ?= ../quant-research-framework
export QRF_PY_DIR
export OPENBLAS_NUM_THREADS = 1   # single-threaded: deterministic + polite on shared boxes

PY := python3

.PHONY: repro parity leak bench sweep test build

build:
	cargo build --release

parity: build
	$(PY) tools/parity_check.py  --csv data/SOLUSDT_1h.csv --tol 0.001
	$(PY) tools/parity_regime.py --csv data/SOLUSDT_1h.csv --tol 0.001
	$(PY) tools/parity_forex.py  --csv data/EURUSD_1h.csv  --tol 0.001

leak:
	$(PY) $(QRF_PY_DIR)/listings/lah_demo.py

bench: build
	$(PY) tools/benchmark.py --check

test: build
	cargo test --release

repro: parity leak
	@echo
	@echo "repro OK, both engines agree within 1e-3 on every parity surface,"
	@echo "and the no-look-ahead guard caught the planted forward-peek bug."

# --- full validation sweep ----------------------------------------------
# Every parity surface on every dataset it supports, plus both test suites,
# the consistency guard, the benchmark drift check and the cross-architecture
# goldens. Slower and broader than `make repro`; this is the release gate.
sweep:
	QRF_PY_DIR=$(QRF_PY_DIR) bash tools/sweep_all.sh
