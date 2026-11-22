import json
import tempfile
from pathlib import Path

from backend.ranking import ranked_teams
from backend.store import Store
from backend.views import team_state_view

from .helpers import make_state, make_team, set_verdict, write_state


def test_survives_a_restart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "session.db")

        first = Store(path)
        write_state(first, make_state([make_team(1)], "jury"))

        def rename(state):
            state.teams[0].pitch.startup_name = "Persisted"

        first.update(rename)
        first.close()

        second = Store(path)
        state = second.read()
        assert state.phase == "jury"
        assert state.teams[0].pitch.startup_name == "Persisted"
        second.close()


def test_creates_a_fresh_lobby_and_increments_the_revision_on_updates() -> None:
    store = Store(":memory:")
    initial = store.read()
    assert initial.phase == "lobby"
    assert initial.revision == 0

    def to_ideation(state):
        state.phase = "ideation"
        return state.revision

    revision = store.update(to_ideation)
    assert revision == 0
    assert store.read().revision == 1
    assert store.read().phase == "ideation"

    store.reset()
    assert store.read().phase == "lobby"
    assert len(store.read().teams) == 0


def test_reconciles_older_stored_decisions_before_ranking_and_display() -> None:
    store = Store(":memory:")
    team = make_team(1)
    set_verdict(
        team,
        {"growth": 7, "finance": 6, "customer": 8, "technology": 5},
        {"growth": "out", "finance": "in", "customer": "out", "technology": "in"},
    )
    write_state(store, make_state([team], "verdict"))

    loaded = store.read()
    decisions = {
        investor_id: loaded.teams[0].verdict[investor_id].decision
        for investor_id in ("growth", "finance", "customer", "technology")
    }
    assert decisions == {
        "growth": "in",
        "finance": "out",
        "customer": "in",
        "technology": "out",
    }
    assert ranked_teams(loaded.teams)[0].in_count == 2
    view = team_state_view(loaded, loaded.teams[0])
    assert view["team"]["verdict"]["growth"]["decision"] == "in"
    assert view["team"]["verdict"]["finance"]["decision"] == "out"
    store.close()


def test_reads_older_sessions_without_one_change() -> None:
    store = Store(":memory:")
    state = make_state([make_team(1)], "defense")
    data = state.model_dump(by_alias=True, exclude_none=True)
    data["teams"][0]["defense"].pop("oneChange", None)
    store._db.execute("UPDATE session_state SET data = ? WHERE id = 1", (json.dumps(data),))

    loaded = store.read()
    assert loaded.teams[0].defense.one_change == ""
    assert loaded.teams[0].defense.answers == ["Answer A", "Answer B", "Answer C", "Answer D"]
    store.close()
