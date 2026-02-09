"""Tests for the principles engine (engine.principles module).

This module tests the PrinciplesEngine class which manages self-learning investment
rules. Principles are distilled from the user's trading journal and experience --
they represent reusable heuristics like "Domain expertise creates durable edge" or
"Insider experience is high-signal" that can be applied to future signal scoring.

The principles system is a key feedback mechanism: principles gain validated_count
when their associated trades succeed and invalidated_count when they fail. Principles
that accumulate too many invalidations can be automatically deactivated.

Tests cover:
    - **Retrieval** (test_get_all_principles): Verifies get_all() returns the
      seeded principles.

    - **Creation** (test_create_principle): Tests creating a new principle with
      specified text, category, origin, and weight. Verifies round-trip through
      the database.

    - **Validation tracking** (test_validate_principle): Tests that validate_principle()
      increments the validated_count counter.

    - **Invalidation tracking** (test_invalidate_principle): Tests that
      invalidate_principle() increments the invalidated_count counter.

    - **Auto-deactivation** (test_deactivate_poor_principle): Tests deactivate_if_poor()
      which deactivates principles that have accumulated too many invalidations
      relative to validations. After 5 invalidations with 0 validations, the
      principle should be deactivated (active=False).

    - **Principle matching** (test_match_principles): Tests match_principles() which
      finds principles relevant to a given signal context (domain, symbol). The
      seeded principles include a 'domain' category principle that should match
      when the context includes domain='AI'.

    - **Score adjustment** (test_apply_to_score): Tests apply_to_score() which
      computes a float adjustment to add to a signal's confidence score based
      on a list of matching principles and their validation ratios.
"""

from __future__ import annotations

from engine.principles import PrinciplesEngine


def test_get_all_principles(seeded_db) -> None:
    """Verify that get_all() returns at least the seeded principles.

    The seeded database contains 2 principles (domain expertise and insider
    experience). This test checks that get_all() returns at least 2 active
    principles.
    """
    pe = PrinciplesEngine(seeded_db)
    principles = pe.get_all()
    assert len(principles) >= 2


def test_create_principle(db) -> None:
    """Verify that create_principle() inserts a new principle and returns its ID.

    Creates a principle with specified text, category='risk', origin='user_input',
    and weight=0.03. Then fetches it by ID and verifies all fields are stored
    correctly. Uses the empty ``db`` fixture since no pre-existing data is needed.
    """
    pe = PrinciplesEngine(db)
    pid = pe.create_principle(
        text="Test principle",
        category="risk",
        origin="user_input",
        weight=0.03,
    )
    assert pid > 0
    p = pe.get_principle(pid)
    assert p["text"] == "Test principle"
    assert p["category"] == "risk"
    assert p["weight"] == 0.03


def test_validate_principle(seeded_db) -> None:
    """Verify that validate_principle() increments the validated_count by 1.

    Retrieves the first seeded principle, records its current validated_count,
    calls validate_principle(), and checks that the count increased by exactly 1.
    """
    pe = PrinciplesEngine(seeded_db)
    principles = pe.get_all()
    pid = principles[0]["id"]
    original_count = principles[0]["validated_count"]

    pe.validate_principle(pid)
    updated = pe.get_principle(pid)
    assert updated["validated_count"] == original_count + 1


def test_invalidate_principle(seeded_db) -> None:
    """Verify that invalidate_principle() increments the invalidated_count by 1.

    The seeded principles start with invalidated_count=0. After one call to
    invalidate_principle(), the count should be 1.
    """
    pe = PrinciplesEngine(seeded_db)
    principles = pe.get_all()
    pid = principles[0]["id"]

    pe.invalidate_principle(pid)
    updated = pe.get_principle(pid)
    assert updated["invalidated_count"] == 1


def test_deactivate_poor_principle(db) -> None:
    """Verify that deactivate_if_poor() deactivates principles with too many failures.

    Creates a fresh principle, invalidates it 5 times (with 0 validations), then
    calls deactivate_if_poor(). The principle should be deactivated (active=False)
    because the invalidation ratio is very high. This prevents poorly-performing
    principles from continuing to influence signal scoring.
    """
    pe = PrinciplesEngine(db)
    pid = pe.create_principle(text="Bad principle", category="test")

    # Invalidate many times
    for _ in range(5):
        pe.invalidate_principle(pid)

    deactivated = pe.deactivate_if_poor(pid)
    assert deactivated
    p = pe.get_principle(pid)
    assert not p["active"]


def test_match_principles(seeded_db) -> None:
    """Verify that match_principles() finds relevant principles for a signal context.

    Provides a context dict with domain='AI' and symbol='NVDA'. The seeded
    principles include a 'domain' category principle ('Domain expertise creates
    durable edge') which should match because 'AI' is in the user's expertise
    domains. Expects at least 1 match.
    """
    pe = PrinciplesEngine(seeded_db)
    matched = pe.match_principles({"domain": "AI", "symbol": "NVDA"})
    # Should match domain + conviction principles
    assert len(matched) >= 1


def test_apply_to_score(seeded_db) -> None:
    """Verify that apply_to_score() returns a float adjustment from matched principles.

    Passes all seeded principles to apply_to_score() and checks that the result
    is a float. With 2 principles where validated_count >= invalidated_count,
    the adjustment should be non-negative (principles with net positive validation
    contribute positively to confidence).
    """
    pe = PrinciplesEngine(seeded_db)
    principles = pe.get_all()
    adjustment = pe.apply_to_score(principles)
    # With 2 validated > invalidated, should be positive
    assert isinstance(adjustment, float)
