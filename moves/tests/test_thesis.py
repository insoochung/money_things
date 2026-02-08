"""Tests for the thesis engine (engine.thesis module)."""

from __future__ import annotations

import pytest

from engine import Thesis, ThesisStatus
from engine.thesis import ThesisEngine


def test_create_thesis(db) -> None:
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
        ),
        user_id=1,
    )
    assert thesis.id is not None
    assert thesis.status == ThesisStatus.ACTIVE


def test_get_thesis(db) -> None:
    engine = ThesisEngine(db)
    created = engine.create_thesis(
        Thesis(title="Test thesis", thesis_text="Some text", symbols=["AAPL"]),
        user_id=1,
    )
    fetched = engine.get_thesis(created.id, user_id=1)
    assert fetched is not None
    assert fetched.title == "Test thesis"
    assert fetched.symbols == ["AAPL"]


def test_list_theses(db) -> None:
    engine = ThesisEngine(db)
    engine.create_thesis(Thesis(title="T1", thesis_text=""), user_id=1)
    engine.create_thesis(Thesis(title="T2", thesis_text=""), user_id=1)
    all_theses = engine.list_theses(user_id=1)
    assert len(all_theses) == 2

    active = engine.list_theses(user_id=1, status=ThesisStatus.ACTIVE)
    assert len(active) == 2


def test_update_thesis(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Original", thesis_text="text", conviction=0.5), user_id=1)
    updated = engine.update_thesis(t.id, user_id=1, title="Updated", conviction=0.9)
    assert updated.title == "Updated"
    assert updated.conviction == 0.9


def test_valid_transitions(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="State test", thesis_text=""), user_id=1)

    t = engine.transition_status(t.id, ThesisStatus.STRENGTHENING, reason="Good news", user_id=1)
    assert t.status == ThesisStatus.STRENGTHENING

    t = engine.transition_status(t.id, ThesisStatus.CONFIRMED, reason="Earnings beat", user_id=1)
    assert t.status == ThesisStatus.CONFIRMED

    t = engine.transition_status(t.id, ThesisStatus.WEAKENING, reason="Guidance cut", user_id=1)
    assert t.status == ThesisStatus.WEAKENING

    t = engine.transition_status(t.id, ThesisStatus.INVALIDATED, reason="Thesis failed", user_id=1)
    assert t.status == ThesisStatus.INVALIDATED

    t = engine.transition_status(t.id, ThesisStatus.ARCHIVED, reason="Done", user_id=1)
    assert t.status == ThesisStatus.ARCHIVED


def test_invalid_transition(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Bad transition", thesis_text=""), user_id=1)

    with pytest.raises(ValueError, match="Invalid transition"):
        engine.transition_status(t.id, ThesisStatus.INVALIDATED, user_id=1)


def test_versioning(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Versioned", thesis_text=""), user_id=1)
    engine.transition_status(t.id, ThesisStatus.STRENGTHENING, reason="R1", user_id=1)
    engine.transition_status(t.id, ThesisStatus.CONFIRMED, reason="R2", user_id=1)

    versions = engine.get_versions(t.id, user_id=1)
    assert len(versions) == 3
    assert versions[1]["old_status"] == "active"
    assert versions[1]["new_status"] == "strengthening"
    assert versions[2]["new_status"] == "confirmed"


def test_add_symbols(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Discovery", thesis_text="", symbols=["NVDA"]), user_id=1)
    updated = engine.add_symbols(t.id, ["AVGO", "MRVL"], user_id=1)
    assert set(updated.symbols) == {"NVDA", "AVGO", "MRVL"}


def test_archived_no_transitions(db) -> None:
    engine = ThesisEngine(db)
    t = engine.create_thesis(Thesis(title="Archive test", thesis_text=""), user_id=1)
    engine.transition_status(t.id, ThesisStatus.ARCHIVED, user_id=1)

    with pytest.raises(ValueError, match="Invalid transition"):
        engine.transition_status(t.id, ThesisStatus.ACTIVE, user_id=1)
