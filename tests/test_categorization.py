"""Tests for the categorisation engine.

The pure functions are tested here (matching, priority, normalisation): no
database required. Protection of manual choices is verified end to end
against the API, because it depends on stored state.
"""

import pytest

from app.models import CategoryRule, Transaction
from app.services.categorization import (
    InvalidRule,
    match_rule,
    merchant_key,
    suggest_pattern,
    validate_pattern,
)


def rule(pattern, category_id=1, priority=100, match_type="contains", field="description", account_id=None):
    r = CategoryRule()
    r.id = priority  # a stable id is enough for ordering in these tests
    r.pattern = pattern
    r.category_id = category_id
    r.priority = priority
    r.match_type = match_type
    r.field = field
    r.account_id = account_id
    r.enabled = True
    return r


def transaction(description="", counterparty=None, account_id=1):
    t = Transaction()
    t.description = description
    t.counterparty = counterparty
    t.account_id = account_id
    return t


# ------------------------------------------------------------- match types


@pytest.mark.parametrize(
    "match_type, pattern, description, expected",
    [
        ("contains", "steam", "Purchase on Steam Store", True),
        ("contains", "steam", "Spotify", False),
        ("starts_with", "amazon", "Amazon Prime", True),
        ("starts_with", "prime", "Amazon Prime", False),
        ("exact", "steam", "Steam", True),
        ("exact", "steam", "Steam Store", False),
        ("regex", r"^riot\s+games$", "Riot Games", True),
        ("regex", r"^riot\s+games$", "Riot Games Store", False),
    ],
)
def test_match_types(match_type, pattern, description, expected):
    r = rule(pattern, match_type=match_type)
    assert (match_rule(transaction(description), [r]) is not None) is expected


def test_matching_is_case_insensitive():
    assert match_rule(transaction("SPOTIFY AB"), [rule("spotify")]) is not None
    assert match_rule(transaction("spotify ab"), [rule("SPOTIFY")]) is not None


def test_an_empty_description_never_matches():
    assert match_rule(transaction(""), [rule("steam")]) is None


# ---------------------------------------------------------------- priority


def test_the_lowest_priority_number_wins():
    """'amazon prime' must beat 'amazon', otherwise subscriptions end up
    filed as purchases."""
    rules = sorted(
        [rule("amazon", category_id=10, priority=100), rule("amazon prime", category_id=20, priority=10)],
        key=lambda r: (r.priority, r.id),
    )
    winner = match_rule(transaction("Amazon Prime"), rules)
    assert winner.category_id == 20


def test_ties_are_resolved_deterministically():
    rules = sorted(
        [rule("apple", category_id=10, priority=100), rule("app", category_id=20, priority=100)],
        key=lambda r: (r.priority, r.id),
    )
    # same priority: the id decides, stably across runs
    assert match_rule(transaction("Apple"), rules) is not None


# ------------------------------------------------------------------ fields


def test_a_rule_on_the_counterparty():
    r = rule("mario rossi", field="counterparty")
    assert match_rule(transaction("Transfer", counterparty="Mario Rossi"), [r]) is not None
    # the same text in the description must not trigger it
    assert match_rule(transaction("Mario Rossi"), [r]) is None


def test_a_rule_limited_to_one_account():
    r = rule("steam", account_id=2)
    assert match_rule(transaction("Steam", account_id=2), [r]) is not None
    assert match_rule(transaction("Steam", account_id=1), [r]) is None


def test_a_global_rule_applies_to_every_account():
    r = rule("steam", account_id=None)
    assert match_rule(transaction("Steam", account_id=99), [r]) is not None


def test_an_invalid_regex_does_not_break_everything():
    """A rule that became invalid is ignored, it does not block the others."""
    broken = rule("([unclosed", match_type="regex", priority=1)
    good = rule("steam", priority=2)
    assert match_rule(transaction("Steam"), [broken, good]).pattern == "steam"


# -------------------------------------------------------------- validation


def test_validate_pattern_rejects_empty():
    with pytest.raises(InvalidRule):
        validate_pattern("contains", "   ")


def test_validate_pattern_rejects_an_invalid_regex():
    with pytest.raises(InvalidRule):
        validate_pattern("regex", "([unclosed")


def test_validate_pattern_accepts_a_valid_regex():
    validate_pattern("regex", r"^amazon\s")


# ------------------------------------------------------------- suggestions


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Top-up by *1234", "top-up by"),
        ("Top-up by *5678", "top-up by"),   # same key: one rule, not two
        ("Steam", "steam"),
        ("Incoming transfer from MARIO ROSSI (IT60X0542811101000000123456)", "incoming transfer from mario rossi"),
        ("Bottega 4821", "bottega"),
    ],
)
def test_merchant_key(description, expected):
    assert merchant_key(description) == expected


def test_merchant_key_on_an_empty_description():
    assert merchant_key(None) == ""
    assert merchant_key("   ") == ""


# --------------------------------------------------------- searchable patterns


@pytest.mark.parametrize(
    "description, expected",
    [
        ("Steam", "steam"),
        ("Bottega 4821", "bottega"),
        ("Top-up by *1234", "top-up by"),
        # merchant_key would give "g a com", which is not in the description
        ("G2a Com", "g2a com"),
        ("Incoming transfer from MARIO ROSSI (IT60X0542811101000000123456)",
         "incoming transfer from mario rossi"),
        ("The Space Cinema", "the space cinema"),
    ],
)
def test_suggest_pattern(description, expected):
    assert suggest_pattern(description) == expected


@pytest.mark.parametrize(
    "description",
    ["Steam", "Bottega 4821", "G2a Com", "Top-up by *1234", "The Space Cinema", "A.b.c."],
)
def test_the_pattern_is_always_contained_in_the_description(description):
    """The property that matters: a "contains" rule built from this pattern
    must find the transaction it came from."""
    assert suggest_pattern(description) in description.lower()


def test_the_pattern_does_not_empty_out_on_all_numeric_descriptions():
    """If stripping the numbers leaves nothing, the full description is safer
    than an empty pattern that would match everything."""
    assert suggest_pattern("12345") == "12345"
