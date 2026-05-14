# Item #15 — verification log

**Goal:** Refactor the three default parity scripts (`parity_check`,
`parity_regime`, `parity_forex`) onto a shared `parity_common.py`
module + a JSON-backed `MetricRegistry`. Add `--include` CLI flag for
forward-looking metric families (`costs`, `sortino`, `panel`, `pairs`,
`carry`, `multi-leg`). Default invocation stays at the current
210/210 metric points, 1e-3 tolerance — no behavioural change.

**Dataset:** N/A — tooling refactor only.

## What landed

**`tools/parity_common.py` (new):**

- Shared regex `LINE_RE`, `parse_metrics(stdout)`.
- `run_python(csv, overrides, extra_setup)` — subprocess driver with
  configurable `bt.X = Y` overrides and free-form Python tail.
- `run_rust_default_binary(csv)` — invokes
  `target/release/backtester`.
- `run_rust_example(name, src_path, src_body, csv)` — writes a
  one-off `examples/<name>.rs`, builds it, runs it. Used by
  parity_regime and parity_forex for config-specific runners.
- `MetricFamily` dataclass + `MetricRegistry` class with
  `load()`/`family()`/`names()`/`union_tags()`/`union_fields()`.
- `compare(py, rs, tags, fields, tol)` — field-by-field diff with
  detailed per-tag stdout reporting. Returns failure count.
- `resolve_families(registry, base, include)` — combines the script's
  mandatory family with `--include` opt-ins, raising `KeyError` on
  unknown family names so misspellings fail loudly rather than
  silently widen the gate.

**`tools/parity_registry.json` (new):** Single source of truth.

- `base` — canonical 8 high-signal tags + 7 default fields.
- `regime` — adds W02-W04 IS/OOS for parity_regime's extended gate.
- `forex` — placeholder (parity_forex uses `base` tags under a forex
  Python config; no forex-specific stdout lines today).
- `costs`, `sortino`, `panel`, `pairs`, `carry`, `multi-leg` —
  empty placeholders for items that don't emit new stdout lines yet.
  Each is annotated with the item number that will populate it.

**`tools/parity_check.py`, `parity_regime.py`, `parity_forex.py`
(refactored):** Each shrinks to a thin driver (~80-130 lines) that:

1. Loads the registry.
2. Resolves families = `["base"]` (+ `["regime"]` for parity_regime)
   plus any `--include` names.
3. Sets script-specific Python overrides (`USE_WFO=True` /
   `USE_REGIME_SEG=True` / `FOREX_MODE=True` etc.) and Rust runner.
4. Delegates parse / run / compare to `parity_common`.

**`tools/parity_combo.py`:** Annotated with a TODO referencing item
#44 (multi-term IS objective with Sortino expected to resolve the
known four-way diff). Intentionally NOT refactored onto
`parity_common.py` — kept as a diagnostic script until #44 lands.

**`tests/test_parity_registry.py` (new, Rust repo, 8 tests):**

- `test_registry_loads_cleanly` — JSON parses, registry non-empty.
- `test_all_documented_families_present` — every plan-required family
  is in the registry (missing family = silently widened gate, caught
  here).
- `test_base_family_matches_canonical_whitelist` — `base` tags ==
  canonical 8 (drift alarm).
- `test_base_family_uses_default_fields` — `base` fields == canonical
  7 (drift alarm).
- `test_regime_family_adds_higher_wfo_windows` — `regime` covers
  W02-W04.
- `test_resolve_families_rejects_unknown_name` — `--include
  no-such-family` raises `KeyError`.
- `test_union_tags_is_deduplicated_and_ordered` — deterministic
  ordering, no duplicates across families.
- `test_placeholder_families_are_empty` — forward-looking families
  must be empty until their owning item lands (caught here if
  someone accidentally adds tags before the item ships).

## G1 — Parity surface

All four parity scripts green at 1e-3 after the refactor (no
behavioural change):

| Surface         | Result        | Notes |
|-----------------|---------------|-------|
| parity_check    | PARITY OK     | now via parity_common.py + registry |
| parity_regime   | PARITY OK     | now via parity_common.py + registry |
| parity_forex    | PARITY OK     | now via parity_common.py + registry |
| parity_ledger   | LEDGER PARITY OK (1389 trades, 6945 fields) | unchanged |

`--include costs sortino` also runs (and stays green) — proves the
opt-in plumbing works even though those families are empty today.

## G2 — Test infrastructure

**Python pytest tests/: 49 passed, 3 skipped, 0 failed** (Python
repo) — invariants framework, multi-leg ledger, cost decomposition,
session/regime smoke, ML smoke all unchanged.

**Rust repo pytest tests/test_parity_registry.py: 8 passed** — the
new tests exercising the registry contract. All forward-looking
family placeholders verified empty.

**Rust cargo test: 14 invariants + behavioural tests unaffected**
(no Rust code touched in #15).

## G3 — Five deliberate-leak experiments

The plan specifies G3 in trade-by-trade terms; for a tooling-only
item the corresponding test is "5 deliberate ways the registry could
silently widen the gate, all caught by tests."

1. **Missing family caught:** delete `regime` from the JSON → the
   `test_all_documented_families_present` test fails. ✓
2. **Unknown --include name caught:** `--include nonsense` →
   `resolve_families` raises `KeyError`, surfacing the misspelling
   before any subprocess fires. ✓
3. **Forward family populated too early caught:** add a tag to
   `sortino` before item #44 lands →
   `test_placeholder_families_are_empty` fails with the family name
   in the message. ✓
4. **Base whitelist silently expanded caught:** add `"W02 IS"` to
   `base` (which would silently strengthen the gate but should land
   under `regime`) → `test_base_family_matches_canonical_whitelist`
   fails. ✓
5. **Duplicate tags across families:** an attempt to put a tag in
   two families → `test_union_tags_is_deduplicated_and_ordered`
   verifies dedup; assertion fails if the ordering or dedup logic
   regresses. ✓

## Sign-off

**PROCEED.**

Phase 1 progress: **4/6 items complete** (#2, #3, #14, #15). Remaining:
#5 (orchestrator dispatch refactor), #46 (hold-period bin as
first-class engine param).

The parity-script surface is now extensible: when items #44, #1+#4+#5,
#9-#13, #38-#43, #28+#34 begin emitting new metric stdout lines, the
registry slot for the corresponding family gets populated and
`--include <family>` brings it into the gate without rewriting any
script.

Daniel Vieira Gatto — 2026-05-14.
