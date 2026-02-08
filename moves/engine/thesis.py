"""Thesis engine: CRUD operations, state machine transitions, and version history.

This module manages investment theses -- the fundamental building blocks of the
money_moves system. Every trading signal and position should be tied to a thesis,
enforcing the "thesis-first" philosophy where macro beliefs drive individual ticker
decisions.

Theses originate in money_thoughts (the thinking module) and are pushed to money_moves
(the execution module) via the API. The thesis engine manages:
    - Creation and retrieval of theses
    - Field updates (title, text, symbols, conviction, etc.)
    - Status transitions through a validated state machine
    - Version history for audit trail and learning
    - Symbol universe management (adding new tickers to a thesis)

State Machine:
    Theses follow a strict state machine defined by VALID_TRANSITIONS:

        ACTIVE --> STRENGTHENING, CONFIRMED, WEAKENING, ARCHIVED
        STRENGTHENING --> CONFIRMED, WEAKENING, ACTIVE, ARCHIVED
        CONFIRMED --> WEAKENING, ARCHIVED
        WEAKENING --> INVALIDATED, STRENGTHENING, ACTIVE, ARCHIVED
        INVALIDATED --> ARCHIVED
        ARCHIVED --> (terminal, no outgoing transitions)

    Invalid transitions raise ValueError. Each transition creates a thesis_versions
    record with the old status, new status, reason, and optional evidence.

    The thesis status directly affects signal confidence scoring through the
    THESIS_STRENGTH multiplier map in engine.signals:
        active=1.0x, strengthening=1.1x, confirmed=1.2x,
        weakening=0.6x, invalidated=0.0x, archived=0.0x

JSON Fields:
    Several thesis fields (symbols, universe_keywords, validation_criteria,
    failure_criteria) are stored as JSON-serialized strings in SQLite and
    deserialized to Python lists on read. The _row_to_thesis() function handles
    this conversion.

Classes:
    ThesisEngine: Main class for thesis CRUD and state machine management.

Functions:
    _row_to_thesis: Convert a database row dict to a Thesis model.
    _audit: Helper to create audit log entries for thesis actions.

Module-level constants:
    VALID_TRANSITIONS: Maps each ThesisStatus to its set of valid target statuses.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from db.database import Database
from engine import ActorType, Thesis, ThesisStatus

logger = logging.getLogger(__name__)

# Valid state transitions for the thesis lifecycle.
# Each key is a current status, and the value is the set of statuses it can transition to.
# ARCHIVED is a terminal state with no outgoing transitions.
# INVALIDATED can only transition to ARCHIVED.
# All non-terminal states can transition to ARCHIVED (allows manual archival at any point).
VALID_TRANSITIONS: dict[ThesisStatus, set[ThesisStatus]] = {
    ThesisStatus.ACTIVE: {
        ThesisStatus.STRENGTHENING,
        ThesisStatus.CONFIRMED,
        ThesisStatus.WEAKENING,
        ThesisStatus.ARCHIVED,
    },
    ThesisStatus.STRENGTHENING: {
        ThesisStatus.CONFIRMED,
        ThesisStatus.WEAKENING,
        ThesisStatus.ACTIVE,
        ThesisStatus.ARCHIVED,
    },
    ThesisStatus.CONFIRMED: {
        ThesisStatus.WEAKENING,
        ThesisStatus.ARCHIVED,
    },
    ThesisStatus.WEAKENING: {
        ThesisStatus.INVALIDATED,
        ThesisStatus.STRENGTHENING,
        ThesisStatus.ACTIVE,
        ThesisStatus.ARCHIVED,
    },
    ThesisStatus.INVALIDATED: {
        ThesisStatus.ARCHIVED,
    },
    ThesisStatus.ARCHIVED: set(),
}


class ThesisEngine:
    """Engine for managing investment theses with state machine lifecycle.

    Provides CRUD operations for theses, validated state transitions, version
    history tracking, and symbol universe management. All mutations are audited
    and committed to the database.

    Attributes:
        db: Database instance used for all persistence operations.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the ThesisEngine with a database connection.

        Args:
            db: Database instance for reading/writing theses, versions, and audit entries.
        """
        self.db = db

    def create_thesis(self, thesis: Thesis) -> Thesis:
        """Create a new thesis and persist it to the database.

        Creates the thesis record, an initial version record (with old_status=NULL),
        and an audit log entry. List fields (symbols, universe_keywords,
        validation_criteria, failure_criteria) are JSON-serialized for storage.

        Args:
            thesis: Thesis model to persist. All fields except id, created_at, and
                updated_at should be populated. The status defaults to ACTIVE.

        Returns:
            The same Thesis model with id, created_at, and updated_at populated.

        Side effects:
            - Inserts a row into the theses table.
            - Inserts an initial thesis_versions record with old_status=NULL.
            - Inserts an audit_log entry with action 'thesis_created'.
            - Commits the database transaction (twice: once for thesis, once for version).
        """
        now = datetime.now(UTC).isoformat()
        cursor = self.db.execute(
            """INSERT INTO theses
               (title, thesis_text, strategy, status, symbols, universe_keywords,
                validation_criteria, failure_criteria, horizon, conviction, source_module,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                thesis.title,
                thesis.thesis_text,
                thesis.strategy,
                thesis.status.value,
                json.dumps(thesis.symbols),
                json.dumps(thesis.universe_keywords),
                json.dumps(thesis.validation_criteria),
                json.dumps(thesis.failure_criteria),
                thesis.horizon,
                thesis.conviction,
                thesis.source_module,
                now,
                now,
            ),
        )
        self.db.connect().commit()
        thesis.id = cursor.lastrowid
        thesis.created_at = now
        thesis.updated_at = now

        # Initial version record
        self.db.execute(
            """INSERT INTO thesis_versions (thesis_id, old_status, new_status, reason)
               VALUES (?, NULL, ?, ?)""",
            (thesis.id, thesis.status.value, "Created"),
        )
        self.db.connect().commit()

        _audit(self.db, "thesis_created", "thesis", thesis.id)
        return thesis

    def get_thesis(self, thesis_id: int) -> Thesis | None:
        """Retrieve a thesis by its database ID.

        Args:
            thesis_id: The primary key ID of the thesis to retrieve.

        Returns:
            Thesis model if found (with JSON fields deserialized), None otherwise.
        """
        row = self.db.fetchone("SELECT * FROM theses WHERE id = ?", (thesis_id,))
        if not row:
            return None
        return _row_to_thesis(row)

    def list_theses(self, status: ThesisStatus | None = None) -> list[Thesis]:
        """List all theses, optionally filtered by status.

        Returns theses ordered by updated_at descending (most recently modified first).

        Args:
            status: Optional ThesisStatus to filter by. If None, returns all theses.

        Returns:
            List of Thesis models matching the filter criteria. Empty list if no matches.
        """
        if status:
            rows = self.db.fetchall(
                "SELECT * FROM theses WHERE status = ? ORDER BY updated_at DESC",
                (status.value,),
            )
        else:
            rows = self.db.fetchall("SELECT * FROM theses ORDER BY updated_at DESC")
        return [_row_to_thesis(r) for r in rows]

    def update_thesis(
        self,
        thesis_id: int,
        *,
        title: str | None = None,
        thesis_text: str | None = None,
        symbols: list[str] | None = None,
        universe_keywords: list[str] | None = None,
        validation_criteria: list[str] | None = None,
        failure_criteria: list[str] | None = None,
        conviction: float | None = None,
        horizon: str | None = None,
    ) -> Thesis | None:
        """Update non-status fields of a thesis.

        Only provided (non-None) fields are updated. The updated_at timestamp is
        always refreshed. List fields are JSON-serialized before storage.

        This method does NOT change the thesis status -- use transition_status() for
        that, which enforces the state machine.

        Args:
            thesis_id: The database ID of the thesis to update.
            title: New title (optional).
            thesis_text: New thesis narrative (optional).
            symbols: New list of ticker symbols (optional).
            universe_keywords: New list of universe keywords (optional).
            validation_criteria: New list of validation criteria (optional).
            failure_criteria: New list of failure criteria (optional).
            conviction: New conviction level 0.0-1.0 (optional).
            horizon: New time horizon string (optional).

        Returns:
            The updated Thesis model, or None if the thesis doesn't exist. Returns
            the unchanged thesis if no fields were provided to update.

        Side effects:
            - Updates the specified fields and updated_at in the theses table.
            - Inserts an audit_log entry with action 'thesis_updated'.
            - Commits the database transaction.
        """
        thesis = self.get_thesis(thesis_id)
        if not thesis:
            return None

        now = datetime.now(UTC).isoformat()
        updates: list[str] = []
        params: list = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if thesis_text is not None:
            updates.append("thesis_text = ?")
            params.append(thesis_text)
        if symbols is not None:
            updates.append("symbols = ?")
            params.append(json.dumps(symbols))
        if universe_keywords is not None:
            updates.append("universe_keywords = ?")
            params.append(json.dumps(universe_keywords))
        if validation_criteria is not None:
            updates.append("validation_criteria = ?")
            params.append(json.dumps(validation_criteria))
        if failure_criteria is not None:
            updates.append("failure_criteria = ?")
            params.append(json.dumps(failure_criteria))
        if conviction is not None:
            updates.append("conviction = ?")
            params.append(conviction)
        if horizon is not None:
            updates.append("horizon = ?")
            params.append(horizon)

        if not updates:
            return thesis

        updates.append("updated_at = ?")
        params.append(now)
        params.append(thesis_id)

        sql = f"UPDATE theses SET {', '.join(updates)} WHERE id = ?"
        self.db.execute(sql, tuple(params))
        self.db.connect().commit()

        _audit(self.db, "thesis_updated", "thesis", thesis_id)
        return self.get_thesis(thesis_id)

    def transition_status(
        self,
        thesis_id: int,
        new_status: ThesisStatus,
        reason: str = "",
        evidence: str = "",
    ) -> Thesis | None:
        """Transition a thesis to a new status, enforcing the state machine.

        Validates that the transition is allowed by VALID_TRANSITIONS before applying
        it. Creates a thesis_versions record documenting the transition with reason
        and evidence. Also creates a detailed audit log entry.

        Args:
            thesis_id: The database ID of the thesis to transition.
            new_status: The target ThesisStatus to transition to.
            reason: Human-readable reason for the transition (e.g., 'Earnings beat
                expectations').
            evidence: Supporting evidence for the transition (e.g., 'NVDA Q1 revenue
                +35% YoY').

        Returns:
            The updated Thesis model with the new status, or None if the thesis
            doesn't exist.

        Raises:
            ValueError: If the transition from the current status to new_status is
                not allowed by VALID_TRANSITIONS.

        Side effects:
            - Updates the thesis status and updated_at in the theses table.
            - Inserts a thesis_versions record with old/new status, reason, evidence.
            - Inserts an audit_log entry with action 'thesis_status_changed' and
              details showing the transition.
            - Commits the database transaction.
        """
        thesis = self.get_thesis(thesis_id)
        if not thesis:
            return None

        current = thesis.status
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"Invalid transition: {current.value} -> {new_status.value}")

        now = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE theses SET status = ?, updated_at = ? WHERE id = ?",
            (new_status.value, now, thesis_id),
        )
        self.db.execute(
            """INSERT INTO thesis_versions
               (thesis_id, old_status, new_status, reason, evidence)
               VALUES (?,?,?,?,?)""",
            (thesis_id, current.value, new_status.value, reason, evidence),
        )
        self.db.connect().commit()

        _audit(
            self.db,
            "thesis_status_changed",
            "thesis",
            thesis_id,
            details=f"{current.value} -> {new_status.value}: {reason}",
        )
        return self.get_thesis(thesis_id)

    def get_versions(self, thesis_id: int) -> list[dict]:
        """Get the version history for a thesis.

        Returns all thesis_versions records for the given thesis, ordered by
        timestamp (oldest first). This provides a complete audit trail of all
        status transitions.

        Args:
            thesis_id: The database ID of the thesis.

        Returns:
            List of version dictionaries with keys: id, thesis_id, old_status,
            new_status, reason, evidence, timestamp. The first entry will have
            old_status=NULL (initial creation).
        """
        return self.db.fetchall(
            "SELECT * FROM thesis_versions WHERE thesis_id = ? ORDER BY timestamp",
            (thesis_id,),
        )

    def add_symbols(self, thesis_id: int, new_symbols: list[str]) -> Thesis | None:
        """Add new ticker symbols to a thesis's symbol universe.

        Merges the new symbols with the existing ones, deduplicating via set union.
        This is used when money_thoughts discovers new tickers aligned with an
        existing thesis.

        Args:
            thesis_id: The database ID of the thesis to update.
            new_symbols: List of ticker symbols to add (e.g., ['AVGO', 'MRVL']).

        Returns:
            The updated Thesis model with the expanded symbols list, or None if
            the thesis doesn't exist.

        Side effects:
            Same as update_thesis() (updates symbols field, refreshes updated_at,
            creates audit entry).
        """
        thesis = self.get_thesis(thesis_id)
        if not thesis:
            return None
        combined = list(set(thesis.symbols + new_symbols))
        return self.update_thesis(thesis_id, symbols=combined)


def _row_to_thesis(row: dict) -> Thesis:
    """Convert a database row dictionary to a Thesis model.

    Handles JSON deserialization of list fields (symbols, universe_keywords,
    validation_criteria, failure_criteria) and enum conversion for status.
    Missing or None values are replaced with sensible defaults.

    Args:
        row: Dictionary from a database query with keys matching theses table columns.

    Returns:
        Thesis model populated from the database row.
    """
    return Thesis(
        id=row["id"],
        title=row["title"],
        thesis_text=row.get("thesis_text", ""),
        strategy=row.get("strategy", "long"),
        status=ThesisStatus(row["status"]),
        symbols=json.loads(row.get("symbols", "[]")),
        universe_keywords=json.loads(row.get("universe_keywords", "[]")),
        validation_criteria=json.loads(row.get("validation_criteria", "[]")),
        failure_criteria=json.loads(row.get("failure_criteria", "[]")),
        horizon=row.get("horizon", ""),
        conviction=row.get("conviction", 0.5),
        source_module=row.get("source_module", ""),
        created_at=row.get("created_at", ""),
        updated_at=row.get("updated_at", ""),
    )


def _audit(
    db: Database,
    action: str,
    entity_type: str,
    entity_id: int | None,
    details: str = "",
) -> None:
    """Create an audit log entry for a thesis engine action.

    Records the action in the audit_log table with the ENGINE actor type.
    This provides a complete trail of all thesis lifecycle events (creation,
    updates, status transitions).

    Args:
        db: Database instance for writing the audit entry.
        action: The action performed (e.g., 'thesis_created', 'thesis_status_changed').
        entity_type: The type of entity affected (always 'thesis' for this module).
        entity_id: The database ID of the affected thesis.
        details: Additional context (e.g., 'active -> strengthening: Earnings beat').

    Side effects:
        - Inserts a row into the audit_log table.
        - Commits the database transaction.
    """
    db.execute(
        """INSERT INTO audit_log (actor, action, details, entity_type, entity_id)
           VALUES (?,?,?,?,?)""",
        (ActorType.ENGINE.value, action, details, entity_type, entity_id),
    )
    db.connect().commit()
