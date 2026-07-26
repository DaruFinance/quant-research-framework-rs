"""Tests for the parity-metric registry.

The registry under ``tools/parity_registry.json`` is the single source
of truth for which metric tags and fields the parity scripts diff per
feature family. These tests assert:

1. The JSON file parses cleanly under ``MetricRegistry.load()``.
2. The mandatory ``base`` family is present and non-empty.
3. Every family name documented in the plan is present (even when
   empty as a placeholder); a missing family would silently widen the
   parity gate when an upstream script asks for it.
4. The ``base`` tag whitelist contains the canonical 8 high-signal
   tags that the pre-#15 scripts inspected.
5. The default field set is the canonical 7 (trades, roi, pf, sharpe,
   win_rate, exp, max_dd). Anything else is a regression.

Run from the Rust repo (where tools/ lives):

    pytest tests/test_parity_registry.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add tools/ to sys.path so we can import parity_common.
HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
sys.path.insert(0, str(TOOLS))

import parity_common as pc  # noqa: E402


REQUIRED_FAMILIES = {
    "base",
    "regime",
    "forex",
    "costs",
    "sortino",
    "panel",
    "pairs",
    "carry",
    "multi-leg",
}

BASE_TAGS_CANONICAL = {
    "IS-raw", "OOS-raw", "IS-opt", "OOS-opt",
    "Baseline IS", "Baseline OOS",
    "W01 IS", "W01 OOS",
}

DEFAULT_FIELDS = {"trades", "roi", "pf", "sharpe", "win_rate", "exp", "max_dd"}


def test_registry_loads_cleanly():
    """JSON file parses and produces a non-empty registry."""
    reg = pc.MetricRegistry.load()
    assert reg.names(), "registry must define at least one family"


def test_all_documented_families_present():
    """Every family the plan references must be in the registry.

    A missing family would cause ``resolve_families`` to raise KeyError
    when an upstream script passed ``--include <name>``. We want this
    test to fail loudly if someone deletes a family from the JSON.
    """
    reg = pc.MetricRegistry.load()
    have = set(reg.names())
    missing = REQUIRED_FAMILIES - have
    assert not missing, f"registry missing required families: {missing}"


def test_base_family_matches_canonical_whitelist():
    """The ``base`` tag set must be exactly the canonical 8."""
    reg = pc.MetricRegistry.load()
    base = reg.family("base")
    assert set(base.tags) == BASE_TAGS_CANONICAL, (
        f"base family tag set drifted; expected {BASE_TAGS_CANONICAL}, "
        f"got {set(base.tags)}"
    )


def test_base_family_uses_default_fields():
    """The ``base`` field set must be the canonical 7. New fields land
    under new families (costs, sortino, ...) not in base."""
    reg = pc.MetricRegistry.load()
    base = reg.family("base")
    assert set(base.fields) == DEFAULT_FIELDS, (
        f"base field set drifted; expected {DEFAULT_FIELDS}, "
        f"got {set(base.fields)}"
    )


def test_regime_family_adds_higher_wfo_windows():
    """``regime`` must contain the W02-W04 IS/OOS pairs that
    parity_regime.py needs to extend the gate to the full WFO surface."""
    reg = pc.MetricRegistry.load()
    regime = reg.family("regime")
    expected = {"W02 IS", "W02 OOS", "W03 IS", "W03 OOS", "W04 IS", "W04 OOS"}
    assert set(regime.tags) == expected, (
        f"regime family tag set drifted; expected {expected}, got {set(regime.tags)}"
    )


def test_resolve_families_rejects_unknown_name():
    """Misspelled --include names must fail loudly rather than be silently
    swallowed (which would let a default-only gate sneak past)."""
    reg = pc.MetricRegistry.load()
    import pytest
    with pytest.raises(KeyError):
        pc.resolve_families(reg, "base", ["this-family-does-not-exist"])


def test_union_tags_is_deduplicated_and_ordered():
    """``union_tags`` over multiple families must return a stable
    ordering with no duplicates so parity scripts produce
    deterministic diffs."""
    reg = pc.MetricRegistry.load()
    tags_single = reg.union_tags(["base"])
    tags_double = reg.union_tags(["base", "base"])
    assert tags_single == tags_double, "duplicate family name must not duplicate tags"
    tags_combo = reg.union_tags(["base", "regime"])
    # base tags come first (insertion order), then regime additions.
    assert tags_combo[: len(tags_single)] == tags_single
    # No duplicates.
    assert len(tags_combo) == len(set(tags_combo))


def test_placeholder_families_are_empty():
    """The forward-looking families are placeholders until their items
    land. If one of them gains tags without the corresponding item
    landing, parity scripts using --include would silently start
    checking those tags — surface that as a test failure here so the
    intent (#1, etc.) is explicit."""
    reg = pc.MetricRegistry.load()
    for name in ("costs", "sortino", "panel", "pairs", "carry", "multi-leg", "forex"):
        fam = reg.family(name)
        assert fam.tags == [], (
            f"family {name!r} has tags before its owning item landed: {fam.tags}. "
            f"If this is intentional, update REQUIRED_FAMILIES / test_placeholder_families_are_empty."
        )
