# Reproducibility entry points for the quant-research-framework-rs paper.
#
# Paper: "A Reproducibility-First Walk-Forward Backtester with
# Tolerance-Bounded Cross-Language Parity" (Vieira Gatto, 2026), Appendix A.
#
#   make repro   # all three parity surfaces + look-ahead leak demo, one summary
#   make parity  # the three cross-language parity surfaces only
#   make leak    # the no-look-ahead invariant ("leak") demo only
#   make bench   # the paper-grade Python-vs-Rust benchmark (n=5)
#
# The parity surfaces require the sibling Python repo
# (../quant-research-framework, or set QRF_PY_DIR) and a Rust toolchain.
# All three compare deterministic metric outputs within a 0.1% (1e-3)
# relative tolerance, matching the paper.

PYTHON ?= python3
TOL    ?= 0.001
CSV    ?= data/SOLUSDT_1h.csv

.PHONY: repro parity leak bench

# --- individual parity surfaces -------------------------------------------
parity:
	@echo "== parity surface 1/3: default config (parity_check.py) =="
	$(PYTHON) tools/parity_check.py --csv $(CSV) --tol $(TOL)
	@echo "== parity surface 2/3: regime+WFO (parity_regime.py) =="
	$(PYTHON) tools/parity_regime.py --csv $(CSV) --tol $(TOL)
	@echo "== parity surface 3/3: forex mode (parity_forex.py) =="
	$(PYTHON) tools/parity_forex.py --tol $(TOL)

# --- no-look-ahead leak demo ----------------------------------------------
# The two `*_no_lookahead` invariant tests pollute every bar past a cut
# point with garbage and assert the pre-cut outputs are byte-identical.
leak:
	@echo "== look-ahead leak demo (tests/invariants.rs) =="
	cargo test --release --test invariants no_lookahead -- --nocapture

# --- paper-grade benchmark (n=5) ------------------------------------------
bench:
	@echo "== paper benchmark: Python vs Rust, n=5 runs =="
	$(PYTHON) tools/bench_paper.py

# --- full reproduction with a single pass/fail summary --------------------
# Runs the three parity surfaces and the leak demo, tallies the outcome of
# each, and prints one PASS/FAIL line (Appendix A "make repro").
repro:
	@set -e; \
	pass=0; fail=0; report=""; \
	run() { \
	  name="$$1"; shift; \
	  echo "== repro: $$name =="; \
	  if "$$@"; then \
	    echo "   -> $$name: PASS"; pass=$$((pass+1)); report="$$report\n  PASS  $$name"; \
	  else \
	    echo "   -> $$name: FAIL"; fail=$$((fail+1)); report="$$report\n  FAIL  $$name"; \
	  fi; \
	}; \
	run "parity_check (default)"  $(PYTHON) tools/parity_check.py  --csv $(CSV) --tol $(TOL); \
	run "parity_regime (regime+WFO)" $(PYTHON) tools/parity_regime.py --csv $(CSV) --tol $(TOL); \
	run "parity_forex (forex mode)" $(PYTHON) tools/parity_forex.py --tol $(TOL); \
	run "leak demo (no-look-ahead)" cargo test --release --test invariants no_lookahead; \
	echo ""; \
	echo "==================== make repro summary ===================="; \
	printf '%b\n' "$$report"; \
	echo "-----------------------------------------------------------"; \
	echo "  $$pass passed, $$fail failed"; \
	if [ "$$fail" -eq 0 ]; then \
	  echo "  REPRO OK"; echo "==========================================================="; \
	else \
	  echo "  REPRO FAIL"; echo "==========================================================="; exit 1; \
	fi
