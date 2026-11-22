"""SQLite persistence for the session state.

Session state lives in one JSON document inside SQLite. At this scale (up
to 20 teams) that is simpler and safer than a relational schema, and SQLite
gives us restart safety with zero external services.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from .archive import winner_name
from .jury.validation import decision_for_score
from .personas import INVESTOR_IDS
from .state import SessionState, create_initial_state

T = TypeVar("T")


@dataclass(frozen=True)
class ArchivedRun:
    """One archived run, without its payload."""

    id: str
    archived_at: int
    phase: str
    team_count: int
    winner: str | None
    votes_cast: int


@dataclass(frozen=True)
class ArchivedRunDetail:
    """One archived run with the stored session and vote counts."""

    summary: ArchivedRun
    state: SessionState
    vote_counts: dict[str, int]


class Store:
    def __init__(self, file_path: str) -> None:
        self._file_path = file_path if file_path == ":memory:" else str(Path(file_path).resolve())
        if self._file_path != ":memory:":
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)

        self._lock = threading.RLock()
        self._db = sqlite3.connect(self._file_path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.execute("PRAGMA busy_timeout = 5000")
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              data TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            )
            """
        )
        # One row per browser. The primary key makes a duplicate vote impossible
        # even when several requests from the same browser arrive at once.
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS votes (
              voter_id TEXT PRIMARY KEY,
              team_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            )
            """
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS votes_team_id ON votes (team_id)")
        # Past runs live next to the live session in the same file, so an
        # existing deployment keeps its data and needs no new infrastructure.
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS session_archives (
              id TEXT PRIMARY KEY,
              archived_at INTEGER NOT NULL,
              phase TEXT NOT NULL,
              team_count INTEGER NOT NULL,
              winner TEXT,
              votes_cast INTEGER NOT NULL,
              data TEXT NOT NULL,
              votes TEXT NOT NULL
            )
            """
        )
        archive_columns = {
            str(row[1])
            for row in self._db.execute("PRAGMA table_info(session_archives)").fetchall()
        }
        if "phase" not in archive_columns:
            self._db.execute("ALTER TABLE session_archives ADD COLUMN phase TEXT")
            for archive_id, data, winner in self._db.execute(
                "SELECT id, data, winner FROM session_archives"
            ).fetchall():
                payload = json.loads(data)
                phase = payload.get("phase") if isinstance(payload, dict) else None
                phase = phase if isinstance(phase, str) else "lobby"
                self._db.execute(
                    "UPDATE session_archives SET phase = ?, winner = ? WHERE id = ?",
                    (phase, winner if phase == "winner" else None, archive_id),
                )
        existing = self._db.execute("SELECT data FROM session_state WHERE id = 1").fetchone()
        if not existing or not existing[0]:
            self._write(create_initial_state(_now_ms()))

    @property
    def path(self) -> str:
        return self._file_path

    def read(self) -> SessionState:
        row = self._db.execute("SELECT data FROM session_state WHERE id = 1").fetchone()
        if not row or not row[0]:
            return create_initial_state(_now_ms())
        data = json.loads(row[0])
        if not isinstance(data, dict):
            return create_initial_state(_now_ms())
        if not isinstance(data.get("leaderboardTeamIds"), list):
            data["leaderboardTeamIds"] = None
        # Sessions created before the pitch order existed have none drawn.
        if not isinstance(data.get("pitchOrderTeamIds"), list):
            data["pitchOrderTeamIds"] = None
        # Sessions created before the finale spotlight existed have no index yet.
        if not isinstance(data.get("finaleFinalistIndex"), (int, float)):
            data["finaleFinalistIndex"] = 0
        state = SessionState.model_validate(data)
        for team in state.teams:
            for investor_id in INVESTOR_IDS:
                entry = team.verdict[investor_id]
                if entry.status == "done" and entry.score is not None:
                    entry.decision = decision_for_score(entry.score)
        return state

    def update(self, mutator: Callable[[SessionState], T]) -> T:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                state = self.read()
                result = mutator(state)
                state.revision += 1
                self._write(state)
                self._db.execute("COMMIT")
                return result
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def reset(self) -> SessionState:
        state, _archive_id = self.reset_with_archive()
        return state

    def reset_with_archive(self) -> tuple[SessionState, str | None]:
        """Archive a non-empty run, then clear the session.

        An empty session has nothing worth keeping, so it is cleared without an
        archive entry. Returns the fresh state and the new archive id, if any.
        """
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                state, vote_counts = self.read_with_votes()
                archive_id = self._insert_archive(state, vote_counts) if state.teams else None
                self._db.execute("DELETE FROM votes")
                fresh = create_initial_state(_now_ms())
                self._write(fresh)
                self._db.execute("COMMIT")
                return fresh, archive_id
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def list_archives(self) -> list[ArchivedRun]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT id, archived_at, phase, team_count, winner, votes_cast
                FROM session_archives
                ORDER BY archived_at DESC, id DESC
                """
            ).fetchall()
        return [
            ArchivedRun(
                id=str(row[0]),
                archived_at=int(row[1]),
                phase=str(row[2]),
                team_count=int(row[3]),
                winner=(str(row[4]) if row[4] is not None and row[2] == "winner" else None),
                votes_cast=int(row[5]),
            )
            for row in rows
        ]

    def read_archive(self, archive_id: str) -> ArchivedRunDetail | None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT id, archived_at, phase, team_count, winner, votes_cast, data, votes
                FROM session_archives WHERE id = ?
                """,
                (archive_id,),
            ).fetchone()
        if row is None:
            return None
        data = json.loads(row[6])
        if not isinstance(data, dict):
            return None
        vote_counts = json.loads(row[7])
        summary = ArchivedRun(
            id=str(row[0]),
            archived_at=int(row[1]),
            phase=str(row[2]),
            team_count=int(row[3]),
            winner=(str(row[4]) if row[4] is not None and row[2] == "winner" else None),
            votes_cast=int(row[5]),
        )
        return ArchivedRunDetail(
            summary=summary,
            state=SessionState.model_validate(data),
            vote_counts=(
                {str(key): int(value) for key, value in vote_counts.items()}
                if isinstance(vote_counts, dict)
                else {}
            ),
        )

    def delete_archive(self, archive_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute("DELETE FROM session_archives WHERE id = ?", (archive_id,))
        return cursor.rowcount > 0

    def _insert_archive(self, state: SessionState, vote_counts: dict[str, int]) -> str:
        archive_id = uuid.uuid4().hex
        archived_at = _now_ms()
        self._db.execute(
            """
            INSERT INTO session_archives
              (id, archived_at, phase, team_count, winner, votes_cast, data, votes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                archive_id,
                archived_at,
                state.phase,
                len(state.teams),
                winner_name(state, vote_counts),
                sum(vote_counts.values()),
                json.dumps(
                    state.model_dump(by_alias=True, exclude_none=True),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                json.dumps(vote_counts, separators=(",", ":")),
            ),
        )
        return archive_id

    def cast_vote(
        self,
        voter_id: str,
        team_id: str,
        gate: Callable[[SessionState], str | None],
    ) -> str | None:
        """Store one vote inside the same transaction as the phase check.

        ``gate`` returns None when the vote is allowed, or a short rejection
        code. Returns None on success, otherwise a code such as
        ``already-voted``. The phase check and the insert share one write
        transaction, so a phase change cannot slip between them.
        """
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                state = self.read()
                rejection = gate(state)
                if rejection:
                    self._db.execute("ROLLBACK")
                    return rejection
                try:
                    self._db.execute(
                        "INSERT INTO votes (voter_id, team_id, created_at) VALUES (?, ?, ?)",
                        (voter_id, team_id, _now_ms()),
                    )
                except sqlite3.IntegrityError:
                    self._db.execute("ROLLBACK")
                    return "already-voted"
                self._db.execute("COMMIT")
                return None
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def read_with_votes(self) -> tuple[SessionState, dict[str, int]]:
        with self._lock:
            return self.read(), self.vote_counts()

    def vote_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT team_id, COUNT(*) FROM votes GROUP BY team_id"
            ).fetchall()
        return {str(team_id): int(count) for team_id, count in rows}

    def vote_total(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) FROM votes").fetchone()
        return int(row[0]) if row else 0

    def vote_for(self, voter_id: str) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT team_id FROM votes WHERE voter_id = ?", (voter_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def close(self) -> None:
        self._db.close()

    def _write(self, state: SessionState) -> None:
        payload = json.dumps(
            state.model_dump(by_alias=True, exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._db.execute(
            """
            INSERT INTO session_state (id, data, updated_at) VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (payload, _now_ms()),
        )


def _now_ms() -> int:
    return int(time.time() * 1000)
