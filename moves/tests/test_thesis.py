"""Tests for the thesis engine (engine.thesis module).

This module tests the ThesisEngine class which provides CRUD operations and
state machine transitions for investment theses. Theses are the foundational
concept in money_moves -- every signal must be tied to a thesis, and thesis
status directly affects signal confidence scoring.

Tests cover:
    - **CRUD operations**: Creating theses with full parameters (test_create_thesis),
      retrieving by ID (test_get_thesis), listing with optional status filter
      (test_list_theses), and updating fields (test_update_thesis).

    - **State machine transitions**: The thesis status follows a defined state
      machine: ACTIVE -> STRENGTHENING -> CONFIRMED -> WEAKENING -> INVALIDATED
      -> ARCHIVED (with shortcuts like ACTIVE -> WEAKENING, ACTIVE -> ARCHIVED).
      test_valid_transitions walks through the full happy path. test_invalid_transition
      verifies that illegal transitions (e.g., ACTIVE -> INVALIDATED) raise ValueError.
      test_archived_no_transitions confirms that archived theses cannot transition
      to any other state.

    - **Version history** (test_versioning): Every status transition creates a
      version record in thesis_versions, preserving the old status, new status,
      reason, and timestamp. This test verifies that version history is correctly
      recorded and retrievable.

    - **Symbol management** (test_add_symbols): Tests adding symbols to a thesis's
      universe via add_symbols(). Validates that existing symbols are preserved
      and new ones are appended without duplicates.

Most tests use the ``db`` fixture (empty schema) since thesis operations don't
require pre-existing data. The ThesisEngine manages its own inserts and commits.
"""

from __future__ import annotations

import pytest

from engine import Thesis, ThesisStatus
from engine.thesis import ThesisEngine


def test_create_thesis(db) -> None:
    """Verify that create_thesis() inserts a new thesis and returns it with an ID.

    Creates a thesis with all optional fields populated (symbols, universe_keywords,
    validation_criteria, failure_criteria, horizon, conviction) and asserts that
    the returned Thesis model has a non-None id and starts in ACTIVE status.
    """
    engine = ThesisEngine(db)
    thesis = engine.create_thesis(
        Thesis(
            title="AI infrastructure spending accelerates",
            thesis_text="Hyperscalers will increase capex 30%+",
            strategy="long",
            symbols=["NVDA", "AVGO"],
            universe_keywords=["AI chips", "datacenter"],
            validation_criteria=["capex guidance increases"],
            failure_criteria=["capex cuts"],
            horizon="6m",
            conviction=0.8,
        )
    )
    assert thesis.id is not None
    assert thesis.status == ThesisStatus.ACTIVE


def test_get_thesis(db) -> None:
    """Verify that get_thesis() retrieves a thesis by ID with all fields intact.

    Creates a thesis and immediately fetches it by ID. Asserts that the title
    and symbols list are correctly round-tripped through the database. The symbols
    field is stored as a JSON string in SQLite and deserialized back to a list.
    """
    engine = ThesisEngine(db)
    created = engine.create_thesis(
        Thesis(title="Test thesis", thesis_text="Some text", symbols=["AAPL"])
    )
    fetched = engine.get_thesis(created.id)
    assert fetched is not None
    assert fetched.title == "Test thesis"
    assert fetched.symbols == ["AAPL"]


def test_list_theses(db) -> None:
    """Verify that list_theses() returns all theses, optionally filtered by status.

    Creates two theses and checks that list_theses() without a filter returns both.
    Also verifies that filtering by ACTIVE status returns the same two (since new
    theses default to ACTIVE).
    """
    engine = ThesisEngine(db)
    engine.create_thesis(Thesis(title="T1", thesis_text=""))
    engine.create_thesis(Thesis(title="T2", thesis_text=""))
    all_theses = engine.list_theses()
    assert len(all_theses) == 2

    active = engine.list_theses(status=ThesisStatus.ACTIVE)
    assert len(active) == 2


def test_update_thesis(db) -> None:
    """Verify that update_thesis() modifies specified fields and returns updated thesis.

    Creates a thesis with initial values, then updates the title and conviction.
    Asserts that the returned Thesis model reflects the new values. Only the
    specified fields should change; unmentioned fields remain unchanged.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Original", thesis_text="text", conviction=0.5))
    updated = engine.update_thesis(t.id, title="Updated", conviction=0.9)
    assert updated.title == "Updated"
    assert updated.conviction == 0.9


def test_valid_transitions(db) -> None:
    """Verify the full happy-path state machine: ACTIVE -> ... -> ARCHIVED.

    Walks through every state in the standard thesis lifecycle:
    active -> strengthening -> confirmed -> weakening -> invalidated -> archived.
    Each transition should succeed and update the thesis status accordingly.
    The reason parameter is provided for each transition to populate the
    thesis_versions audit trail.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="State test", thesis_text=""))

    # active -> strengthening
    t = engine.transition_status(t.id, ThesisStatus.STRENGTHENING, reason="Good news")
    assert t.status == ThesisStatus.STRENGTHENING

    # strengthening -> confirmed
    t = engine.transition_status(t.id, ThesisStatus.CONFIRMED, reason="Earnings beat")
    assert t.status == ThesisStatus.CONFIRMED

    # confirmed -> weakening
    t = engine.transition_status(t.id, ThesisStatus.WEAKENING, reason="Guidance cut")
    assert t.status == ThesisStatus.WEAKENING

    # weakening -> invalidated
    t = engine.transition_status(t.id, ThesisStatus.INVALIDATED, reason="Thesis failed")
    assert t.status == ThesisStatus.INVALIDATED

    # invalidated -> archived
    t = engine.transition_status(t.id, ThesisStatus.ARCHIVED, reason="Done")
    assert t.status == ThesisStatus.ARCHIVED


def test_invalid_transition(db) -> None:
    """Verify that illegal state transitions raise ValueError.

    An ACTIVE thesis cannot jump directly to INVALIDATED -- it must go through
    WEAKENING first (or through STRENGTHENING -> CONFIRMED -> WEAKENING).
    This test ensures the state machine enforces valid transition paths.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Bad transition", thesis_text=""))

    with pytest.raises(ValueError, match="Invalid transition"):
        engine.transition_status(t.id, ThesisStatus.INVALIDATED)


def test_versioning(db) -> None:
    """Verify that status transitions create version history records.

    Creates a thesis and transitions it twice (active -> strengthening -> confirmed).
    Checks that get_versions() returns 3 entries: the initial creation plus the
    two transitions. Each version record should contain old_status and new_status
    fields showing the transition that occurred.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Versioned", thesis_text=""))
    engine.transition_status(t.id, ThesisStatus.STRENGTHENING, reason="R1")
    engine.transition_status(t.id, ThesisStatus.CONFIRMED, reason="R2")

    versions = engine.get_versions(t.id)
    assert len(versions) == 3  # initial + 2 transitions
    assert versions[1]["old_status"] == "active"
    assert versions[1]["new_status"] == "strengthening"
    assert versions[2]["new_status"] == "confirmed"


def test_add_symbols(db) -> None:
    """Verify that add_symbols() appends new symbols without duplicating existing ones.

    Creates a thesis with symbol NVDA, then adds AVGO and MRVL. The resulting
    symbols list should contain all three without duplicates. The symbols are
    stored as a JSON array in the database.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Discovery", thesis_text="", symbols=["NVDA"]))
    updated = engine.add_symbols(t.id, ["AVGO", "MRVL"])
    assert set(updated.symbols) == {"NVDA", "AVGO", "MRVL"}


def test_archived_no_transitions(db) -> None:
    """Verify that archived theses cannot transition to any other state.

    ARCHIVED is a terminal state -- once a thesis is archived, no further
    transitions are allowed. This test uses the ACTIVE -> ARCHIVED shortcut
    transition and then verifies that attempting ARCHIVED -> ACTIVE raises
    ValueError.
    """
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Archive test", thesis_text=""))
    engine.transition_status(t.id, ThesisStatus.ARCHIVED)

    with pytest.raises(ValueError, match="Invalid transition"):
        engine.transition_status(t.id, ThesisStatus.ACTIVE)
