from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import load_config
from backend.main import create_app
from backend.store import Store

from .helpers import make_state, make_team, set_verdict, write_state

PASSWORD = "test-password"
INVESTOR_NAMES = {
    "growth": "Victoria Lyon",
    "finance": "Maximilian Löwenstein",
    "customer": "Sophie Leone",
    "technology": "Dr. Alex Leander",
}


def _app():  # type: ignore[no-untyped-def]
    return create_app(
        load_config(
            {
                "DEMO_MODE": "true",
                "MODERATOR_PASSWORD": PASSWORD,
                "SESSION_DB": ":memory:",
            }
        )
    )


def _moderator(client: TestClient) -> TestClient:
    login = client.post("/api/moderator/login", json={"password": PASSWORD})
    assert login.status_code == 200
    return client


def _teams():  # type: ignore[no-untyped-def]
    first = make_team(1, name="Löwen & Söhne")
    first.pitch.startup_name = "Müller AI"
    first.pitch.problem = 'Returns cost margin.\n"Every" return must be handled.'
    first.pitch.tagline = "Die Rückgabe, die sich rechnet."
    first.defense.answers = [
        "We start with three Munich retailers.",
        "A per-store fee covers the first year.",
        "Returns fall by a third in the pilot.",
        "The data stays inside the retailer account.",
    ]
    first.defense.one_change = "Narrow the first customer to independent pharmacies."

    second = make_team(2, name="Northstar Labs")
    second.pitch.startup_name = "Northstar"
    second.defense.answers = ["Answer 1", "Answer 2", "Answer 3", "Answer 4"]
    second.defense.one_change = "Charge per answered audit."

    set_verdict(
        first,
        {"growth": 9, "finance": 8, "customer": 7, "technology": 6},
        {"growth": "in", "finance": "in", "customer": "in", "technology": "out"},
    )
    set_verdict(
        second,
        {"growth": 4, "finance": 5, "customer": 6, "technology": 7},
        {"growth": "out", "finance": "out", "customer": "out", "technology": "in"},
    )
    for investor_id in ("growth", "finance", "customer", "technology"):
        first.round1[investor_id].status = "done"
        first.round1[investor_id].score = {
            "growth": 5,
            "finance": 6,
            "customer": 7,
            "technology": 8,
        }[investor_id]
        first.round1[investor_id].assessment = f"Assessment from {investor_id}."
        first.round1[investor_id].question = f"Question from {investor_id}?"
    return first, second


def _seed(runtime, phase: str = "winner"):  # type: ignore[no-untyped-def]
    first, second = _teams()
    write_state(runtime.store, make_state([first, second], phase))
    for index, (voter, team) in enumerate([("v1", first), ("v2", first), ("v3", second)], start=1):
        runtime.store._db.execute(
            "INSERT INTO votes (voter_id, team_id, created_at) VALUES (?, ?, ?)",
            (voter, team.id, 1_700_000_000_000 + index),
        )
    return first, second


def _reset(client: TestClient) -> dict:
    response = client.post("/api/moderator/reset", json={"confirm": "RESET"})
    assert response.status_code == 200, response.text
    return response.json()


def test_json_export_is_complete_and_versioned() -> None:
    app = _app()
    first, second = _seed(app.state.runtime)
    client = _moderator(TestClient(app))

    response = client.get("/api/moderator/export?format=json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert (
        'filename="ai-lions-den-run-' in response.headers["content-disposition"]
        and '.json"' in response.headers["content-disposition"]
    )
    document = response.json()
    assert document["schemaVersion"] == "1.0"
    assert document["kind"] == "ai-lions-den-run"
    assert document["generatedAt"].endswith("Z")
    assert document["source"] == {"kind": "current", "archiveId": None, "archivedAt": None}
    assert document["session"]["phase"] == "winner"
    assert document["session"]["teams"] == 2
    assert document["session"]["votesCast"] == 3

    team = next(entry for entry in document["teams"] if entry["id"] == first.id)
    assert team["name"] == "Löwen & Söhne"
    assert team["pitch"]["startupName"] == "Müller AI"
    assert team["round1"]["growth"]["score"] == 5
    assert team["round1"]["growth"]["assessment"] == "Assessment from growth."
    assert team["round1"]["growth"]["question"] == "Question from growth?"
    assert team["defense"]["answers"]["growth"] == "We start with three Munich retailers."
    assert team["defense"]["answers"]["technology"] == (
        "The data stays inside the retailer account."
    )
    assert team["defense"]["oneChange"] == "Narrow the first customer to independent pharmacies."
    assert team["verdict"]["growth"] == {
        "status": "done",
        "score": 9,
        "previousScore": 5,
        "decision": "in",
        "statement": "Statement from growth.",
    }
    assert team["total"] == 30
    assert team["deals"] == 3
    assert team["rank"] == 1
    assert team["audienceVotes"] == 2

    assert document["ranking"][0]["teamId"] == first.id
    assert document["ranking"][1]["teamId"] == second.id
    assert document["winner"]["teamId"] == first.id
    assert document["winner"]["votes"] == 2
    assert {entry["id"] for entry in document["investors"]} == {
        "growth",
        "finance",
        "customer",
        "technology",
    }


def test_pre_reveal_exports_do_not_mark_an_audience_winner() -> None:
    app = _app()
    runtime = app.state.runtime
    _seed(runtime, "defense")
    client = _moderator(TestClient(app))

    current = client.get("/api/moderator/export?format=json")
    assert current.status_code == 200
    current_document = current.json()
    assert current_document["winner"] is None
    assert all(not entry["isWinner"] for entry in current_document["audience"])

    archive_id = _reset(client)["archivedId"]
    archived = client.get(f"/api/moderator/archives/{archive_id}/export?format=json")
    assert archived.status_code == 200
    archived_document = archived.json()
    assert archived_document["winner"] is None
    assert all(not entry["isWinner"] for entry in archived_document["audience"])


def test_csv_export_is_excel_friendly_and_quotes_multiline_text() -> None:
    app = _app()
    first, second = _seed(app.state.runtime)
    client = _moderator(TestClient(app))

    response = client.get("/api/moderator/export?format=csv")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="ai-lions-den-run-' in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]
    raw = response.content
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw

    text = raw.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text, newline="")))
    header = rows[0]
    assert header[0] == "Rank"
    assert header[1] == "Team"
    assert header[2] == "Startup"
    assert "Answer - Victoria Lyon" in header
    assert "Our one change" in header
    assert "Round 1 assessment - Maximilian Löwenstein" in header
    assert "Round 1 question - Maximilian Löwenstein" in header
    assert "Round 1 score - Maximilian Löwenstein" in header
    assert "Final score - Dr. Alex Leander" in header
    assert "Decision - Sophie Leone" in header
    assert "Verdict statement - Victoria Lyon" in header
    assert header[-3:] == ["Final total", "Deals (I'm in)", "Audience votes"]
    assert len(rows) == 1 + 2

    first_row = next(row for row in rows[1:] if row[header.index("Team")] == "Löwen & Söhne")
    cells = dict(zip(header, first_row, strict=True))
    assert cells["Rank"] == "1"
    assert cells["Startup"] == "Müller AI"
    assert cells["Tagline"] == "Die Rückgabe, die sich rechnet."
    assert cells["Problem"] == 'Returns cost margin.\n"Every" return must be handled.'
    assert cells["Answer - Victoria Lyon"] == "We start with three Munich retailers."
    assert cells["Answer - Dr. Alex Leander"] == "The data stays inside the retailer account."
    assert cells["Our one change"] == "Narrow the first customer to independent pharmacies."
    assert cells["Round 1 assessment - Victoria Lyon"] == "Assessment from growth."
    assert cells["Round 1 question - Victoria Lyon"] == "Question from growth?"
    assert cells["Round 1 score - Victoria Lyon"] == "5"
    assert cells["Final score - Victoria Lyon"] == "9"
    assert cells["Decision - Victoria Lyon"] == "in"
    assert cells["Verdict statement - Victoria Lyon"] == "Statement from growth."
    assert cells["Decision - Dr. Alex Leander"] == "out"
    assert cells["Final total"] == "30"
    assert cells["Deals (I'm in)"] == "3"
    assert cells["Audience votes"] == "2"

    second_row = next(row for row in rows[1:] if row[header.index("Team")] == "Northstar Labs")
    assert dict(zip(header, second_row, strict=True))["Rank"] == "2"
    assert second.id != first.id  # both teams are in the file


def test_csv_export_neutralizes_formula_cells_for_current_and_archived_runs() -> None:
    app = _app()
    runtime = app.state.runtime
    first, _second = _seed(runtime)

    def add_formula_values(state):  # type: ignore[no-untyped-def]
        team = next(team for team in state.teams if team.id == first.id)
        team.pitch.startup_name = '=HYPERLINK("https://attacker.example")'
        team.pitch.problem = "+SUM(1,1)"
        team.defense.answers[0] = "@command"
        team.defense.one_change = "-change"
        team.round1["growth"].assessment = "=assessment"
        team.round1["growth"].question = "+question"
        team.verdict["growth"].statement = "@statement"

    runtime.store.update(add_formula_values)
    client = _moderator(TestClient(app))

    def first_row(response):  # type: ignore[no-untyped-def]
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))
        header = rows[0]
        return dict(zip(header, rows[1], strict=True))

    current = first_row(client.get("/api/moderator/export?format=csv"))
    assert current["Startup"] == '\'=HYPERLINK("https://attacker.example")'
    assert current["Problem"] == "'+SUM(1,1)"
    assert current["Answer - Victoria Lyon"] == "'@command"
    assert current["Our one change"] == "'-change"
    assert current["Round 1 assessment - Victoria Lyon"] == "'=assessment"
    assert current["Round 1 question - Victoria Lyon"] == "'+question"
    assert current["Verdict statement - Victoria Lyon"] == "'@statement"

    archive_id = _reset(client)["archivedId"]
    archived = first_row(client.get(f"/api/moderator/archives/{archive_id}/export?format=csv"))
    assert archived["Startup"] == current["Startup"]
    assert archived["Problem"] == current["Problem"]
    assert archived["Answer - Victoria Lyon"] == current["Answer - Victoria Lyon"]
    assert (
        archived["Verdict statement - Victoria Lyon"]
        == current["Verdict statement - Victoria Lyon"]
    )


def test_reset_archives_the_run_and_skips_an_empty_session() -> None:
    app = _app()
    runtime = app.state.runtime
    first, _second = _seed(runtime)
    client = _moderator(TestClient(app))

    assert client.get("/api/moderator/archives").json() == {"archives": []}
    reset = _reset(client)

    assert reset["archivedId"]
    assert runtime.store.read().teams == []
    assert runtime.store.vote_total() == 0

    archives = client.get("/api/moderator/archives").json()["archives"]
    assert len(archives) == 1
    archive = archives[0]
    assert archive["id"] == reset["archivedId"]
    assert archive["phase"] == "winner"
    assert archive["teamCount"] == 2
    assert archive["winner"] == "Löwen & Söhne"
    assert archive["votesCast"] == 3
    assert isinstance(archive["archivedAt"], int)

    detail = client.get(f"/api/moderator/archives/{archive['id']}").json()
    assert detail["phase"] == "winner"
    assert detail["teamCount"] == 2
    assert detail["votesCast"] == 3
    assert detail["winner"]["teamId"] == first.id
    assert detail["ranking"][0]["teamId"] == first.id
    assert detail["teams"][0]["pitch"]["startupName"] == "Müller AI"
    assert detail["teams"][0]["verdict"]["growth"]["score"] == 9
    assert detail["teams"][0]["audienceVotes"] == 2

    run_export = client.get(f"/api/moderator/archives/{archive['id']}/export?format=json").json()
    assert run_export["source"] == {
        "kind": "archive",
        "archiveId": archive["id"],
        "archivedAt": run_export["source"]["archivedAt"],
    }
    assert run_export["source"]["archivedAt"].endswith("Z")
    stored_team = next(entry for entry in run_export["teams"] if entry["id"] == first.id)
    assert stored_team["defense"]["oneChange"] == (
        "Narrow the first customer to independent pharmacies."
    )

    archive_csv = client.get(f"/api/moderator/archives/{archive['id']}/export?format=csv")
    assert archive_csv.status_code == 200
    assert archive_csv.content.startswith(b"\xef\xbb\xbf")
    assert "Löwen & Söhne" in archive_csv.content.decode("utf-8-sig")

    empty_reset = _reset(client)
    assert empty_reset["archivedId"] is None
    assert len(client.get("/api/moderator/archives").json()["archives"]) == 1


def test_reset_rolls_back_archive_and_clear_together() -> None:
    store = Store(":memory:")
    state = make_state([make_team(1)], "defense")
    write_state(store, state)
    store._db.execute(
        "INSERT INTO votes (voter_id, team_id, created_at) VALUES (?, ?, ?)",
        ("voter", state.teams[0].id, 1_700_000_000_000),
    )
    original_write = store._write

    def fail_when_writing_fresh_state(next_state):  # type: ignore[no-untyped-def]
        if not next_state.teams:
            raise RuntimeError("fresh state write failed")
        original_write(next_state)

    store._write = fail_when_writing_fresh_state  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="fresh state write failed"):
            store.reset_with_archive()

        assert [team.id for team in store.read().teams] == [state.teams[0].id]
        assert store.vote_counts() == {state.teams[0].id: 1}
        assert store.list_archives() == []
    finally:
        store.close()


def test_archive_delete_removes_only_that_run() -> None:
    app = _app()
    runtime = app.state.runtime
    _seed(runtime)
    client = _moderator(TestClient(app))
    first_id = _reset(client)["archivedId"]
    _seed(runtime)
    second_id = _reset(client)["archivedId"]
    assert len(client.get("/api/moderator/archives").json()["archives"]) == 2

    deleted = client.delete(f"/api/moderator/archives/{first_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    remaining = client.get("/api/moderator/archives").json()["archives"]
    assert [entry["id"] for entry in remaining] == [second_id]
    assert client.get(f"/api/moderator/archives/{first_id}").status_code == 404
    assert client.delete(f"/api/moderator/archives/{first_id}").status_code == 404
    assert client.get(f"/api/moderator/archives/{second_id}").status_code == 200


def test_export_and_archive_routes_require_the_moderator() -> None:
    app = _app()
    runtime = app.state.runtime
    _seed(runtime)
    moderator = _moderator(TestClient(app))
    archive_id = _reset(moderator)["archivedId"]

    paths = [
        ("GET", "/api/moderator/export?format=json"),
        ("GET", "/api/moderator/export?format=csv"),
        ("GET", "/api/moderator/archives"),
        ("GET", f"/api/moderator/archives/{archive_id}"),
        ("GET", f"/api/moderator/archives/{archive_id}/export?format=json"),
        ("GET", f"/api/moderator/archives/{archive_id}/export?format=csv"),
        ("DELETE", f"/api/moderator/archives/{archive_id}"),
    ]
    anonymous = TestClient(app)
    for method, path in paths:
        response = anonymous.request(method, path)
        assert response.status_code == 401, (method, path, response.text)

    # The archive is still there after the rejected delete.
    assert len(moderator.get("/api/moderator/archives").json()["archives"]) == 1


def test_public_views_never_expose_archived_runs() -> None:
    app = _app()
    runtime = app.state.runtime
    _seed(runtime)
    client = _moderator(TestClient(app))
    archive_id = _reset(client)["archivedId"]
    assert archive_id

    for path in ("/api/team/state", "/api/screen/state", "/api/vote/state", "/api/health"):
        response = TestClient(app).get(path)
        assert response.status_code == 200, path
        body = response.text
        assert archive_id not in body
        assert "archives" not in body
        assert "Löwen & Söhne" not in body


def test_rejects_an_unknown_export_format() -> None:
    client = _moderator(TestClient(_app()))
    response = client.get("/api/moderator/export?format=xml")
    assert response.status_code == 400
    assert response.json() == {"error": "Format must be json or csv."}


def test_migrates_an_existing_session_database(tmp_path: Path) -> None:
    path = tmp_path / "session.db"
    state = make_state([make_team(1)], "defense")
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE session_state (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          data TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE votes (
          voter_id TEXT PRIMARY KEY,
          team_id TEXT NOT NULL,
          created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute("CREATE INDEX votes_team_id ON votes (team_id)")
    connection.execute(
        "INSERT INTO session_state (id, data, updated_at) VALUES (1, ?, ?)",
        (json.dumps(state.model_dump(by_alias=True, exclude_none=True)), 123),
    )
    connection.commit()
    connection.close()

    store = Store(str(path))
    try:
        loaded = store.read()
        assert loaded.phase == "defense"
        assert loaded.teams[0].name == "Team 1"

        tables = {
            row[0]
            for row in store._db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "session_archives" in tables

        fresh, archive_id = store.reset_with_archive()
        assert fresh.teams == []
        assert archive_id is not None
        archived = store.list_archives()
        assert len(archived) == 1
        assert archived[0].phase == "defense"
        assert archived[0].team_count == 1
        # A run reset before any final verdict has no winner to report.
        assert archived[0].winner is None
        detail = store.read_archive(archive_id)
        assert detail is not None
        assert detail.state.teams[0].name == "Team 1"
    finally:
        store.close()


def test_migrates_phase_for_an_existing_archive_table(tmp_path: Path) -> None:
    path = tmp_path / "session.db"
    state = make_state([make_team(1)], "defense")
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE session_state (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          data TEXT NOT NULL,
          updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE votes (
          voter_id TEXT PRIMARY KEY,
          team_id TEXT NOT NULL,
          created_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE session_archives (
          id TEXT PRIMARY KEY,
          archived_at INTEGER NOT NULL,
          team_count INTEGER NOT NULL,
          winner TEXT,
          votes_cast INTEGER NOT NULL,
          data TEXT NOT NULL,
          votes TEXT NOT NULL
        )
        """
    )
    payload = json.dumps(state.model_dump(by_alias=True, exclude_none=True))
    connection.execute(
        "INSERT INTO session_state (id, data, updated_at) VALUES (1, ?, ?)",
        (payload, 123),
    )
    connection.execute(
        """
        INSERT INTO session_archives
          (id, archived_at, team_count, winner, votes_cast, data, votes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("legacy", 456, 1, "Old leader", 0, payload, "{}"),
    )
    connection.commit()
    connection.close()

    store = Store(str(path))
    try:
        archives = store.list_archives()
        assert len(archives) == 1
        assert archives[0].id == "legacy"
        assert archives[0].phase == "defense"
        assert archives[0].winner is None
        detail = store.read_archive("legacy")
        assert detail is not None
        assert detail.summary.phase == "defense"
        assert detail.state.phase == "defense"
    finally:
        store.close()
