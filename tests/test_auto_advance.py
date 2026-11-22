"""The jury round advances to the defense by itself once every pitch has been judged."""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.auto_advance import apply_auto_advance, auto_advance_action, jury_round_is_complete
from backend.jury.types import Round1Input, Round1Output
from backend.personas import INVESTOR_IDS
from backend.phase_transition import advance_phase, retreat_phase
from backend.state import Round1Entry, SessionState, Team
from backend.views import session_view

from .helpers import COMPLETE_PITCH, TEST_MODERATOR_PASSWORD, demo_app, make_state, make_team

DELAY = 15_000
NOW = 1_700_000_000_000
HOLD = "/api/moderator/phase/hold"
FORWARD = "/api/moderator/phase"
BACK = "/api/moderator/phase/back"


def _finish_round1(team: Team) -> Team:
    for investor_id in INVESTOR_IDS:
        team.round1[investor_id] = Round1Entry(
            status="done", score=8, assessment="Solid.", question="Why now?"
        )
    return team


def _jury_state(*teams: Team, phase: str = "jury") -> SessionState:
    return make_state(list(teams), phase, now=NOW)


# -- unit level: when the countdown starts and fires ----------------------------------


def test_the_countdown_arms_only_when_every_eligible_team_is_complete() -> None:
    done = _finish_round1(make_team(1))
    waiting = make_team(2)
    state = _jury_state(done, waiting)

    assert jury_round_is_complete(state) is False
    assert apply_auto_advance(state, NOW, DELAY) is False
    assert state.auto_advance_at is None

    _finish_round1(waiting)
    assert jury_round_is_complete(state) is True
    assert apply_auto_advance(state, NOW + 5, DELAY) is False
    assert state.auto_advance_at == NOW + 5 + DELAY
    assert state.phase == "jury"


def test_running_calls_keep_the_countdown_from_starting() -> None:
    team = _finish_round1(make_team(1))
    team.round1[INVESTOR_IDS[0]] = Round1Entry(status="running", started_at=NOW)
    state = _jury_state(team)

    apply_auto_advance(state, NOW, DELAY)
    assert state.auto_advance_at is None


def test_teams_that_were_not_evaluated_do_not_block_the_countdown() -> None:
    state = _jury_state(
        _finish_round1(make_team(1)),
        make_team(2, eligible=False),
    )

    apply_auto_advance(state, NOW, DELAY)
    assert state.auto_advance_at == NOW + DELAY


def test_with_no_eligible_team_the_session_never_advances_by_itself() -> None:
    state = _jury_state(make_team(1, eligible=False))
    assert apply_auto_advance(state, NOW, DELAY) is False
    assert state.auto_advance_at is None
    assert apply_auto_advance(_jury_state(), NOW + 10 * DELAY, DELAY) is False


def test_a_failed_jury_call_means_no_countdown_until_the_retry_completes() -> None:
    team = _finish_round1(make_team(1))
    team.round1[INVESTOR_IDS[0]] = Round1Entry(status="failed", error="Timeout")
    state = _jury_state(team)

    apply_auto_advance(state, NOW, DELAY)
    assert state.auto_advance_at is None
    # Even a long wait never advances a session with a failed call.
    assert apply_auto_advance(state, NOW + 100 * DELAY, DELAY) is False
    assert state.phase == "jury"

    # The retry succeeds: now the countdown arms normally.
    _finish_round1(team)
    apply_auto_advance(state, NOW + 200 * DELAY, DELAY)
    assert state.auto_advance_at == NOW + 201 * DELAY


def test_a_failure_after_arming_cancels_the_countdown() -> None:
    team = _finish_round1(make_team(1))
    state = _jury_state(team)
    apply_auto_advance(state, NOW, DELAY)
    assert state.auto_advance_at == NOW + DELAY

    team.round1[INVESTOR_IDS[1]] = Round1Entry(status="failed", error="Broken")
    assert apply_auto_advance(state, NOW + DELAY + 1, DELAY) is False
    assert state.auto_advance_at is None
    assert state.phase == "jury"


def test_the_countdown_fires_once_at_its_deadline_and_never_early() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    apply_auto_advance(state, NOW, DELAY)
    deadline = state.auto_advance_at
    assert deadline == NOW + DELAY

    assert apply_auto_advance(state, deadline - 1, DELAY) is False
    assert state.phase == "jury"

    assert apply_auto_advance(state, deadline, DELAY) is True
    assert state.phase == "defense"
    assert state.phase_started_at == deadline
    assert state.auto_advance_at is None

    # A second caller racing the first one finds the defense and does nothing.
    assert apply_auto_advance(state, deadline + 1, DELAY) is False
    assert state.phase == "defense"


def test_only_the_jury_round_advances_automatically() -> None:
    for phase in ("lobby", "ideation", "defense", "verdict", "leaderboard", "finale", "voting"):
        state = _jury_state(_finish_round1(make_team(1)), phase=phase)
        assert auto_advance_action(state, NOW + 100 * DELAY, DELAY) is None
        assert apply_auto_advance(state, NOW + 100 * DELAY, DELAY) is False
        assert state.phase == phase


def test_a_manual_advance_ends_the_countdown_and_the_timer_cannot_advance_twice() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    apply_auto_advance(state, NOW, DELAY)
    assert state.auto_advance_at is not None

    assert advance_phase(state, NOW + 1_000).ok is True
    assert state.phase == "defense"
    assert state.auto_advance_at is None

    # The server timer wakes up afterwards: the defense must not be skipped.
    assert apply_auto_advance(state, NOW + DELAY + 1, DELAY) is False
    assert state.phase == "defense"


def test_a_disabled_countdown_never_arms() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    assert apply_auto_advance(state, NOW, 0) is False
    assert state.auto_advance_at is None


def test_a_held_visit_stays_manual() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    apply_auto_advance(state, NOW, DELAY)
    state.auto_advance_at = None
    state.auto_advance_held = True

    assert apply_auto_advance(state, NOW + 100 * DELAY, DELAY) is False
    assert state.auto_advance_at is None
    assert state.phase == "jury"


def test_stepping_back_into_the_jury_does_not_arm_the_countdown() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    assert advance_phase(state, NOW + 1).phase == "defense"
    assert retreat_phase(state, NOW + 2).phase == "jury"
    assert state.auto_advance_held is True

    assert apply_auto_advance(state, NOW + 3, DELAY) is False
    assert state.auto_advance_at is None
    assert apply_auto_advance(state, NOW + 100 * DELAY, DELAY) is False
    assert state.phase == "jury"


def test_entering_the_jury_again_going_forward_is_a_fresh_visit() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    retreat_phase(state, NOW + 1)  # jury -> ideation
    assert state.phase == "ideation"
    assert advance_phase(state, NOW + 2).phase == "jury"
    assert state.auto_advance_held is False

    apply_auto_advance(state, NOW + 3, DELAY)
    assert state.auto_advance_at == NOW + 3 + DELAY


def test_the_session_view_carries_the_deadline_only_during_the_jury_round() -> None:
    state = _jury_state(_finish_round1(make_team(1)))
    apply_auto_advance(state, NOW, DELAY)
    view = session_view(state)
    assert view["autoAdvanceAt"] == NOW + DELAY
    assert view["autoAdvanceHeld"] is False

    state.phase = "defense"
    view = session_view(state)
    assert view["autoAdvanceAt"] is None
    assert view["autoAdvanceHeld"] is False


# -- through the real app: server timer, moderator controls, restart --------------------


def _login(client: TestClient) -> None:
    response = client.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
    assert response.status_code == 200, response.text


def _pitch() -> dict[str, str]:
    return COMPLETE_PITCH.model_dump(by_alias=True)


def _view(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/moderator/state")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for(predicate: Callable[[], bool], timeout_s: float = 10) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Timed out waiting for the server state.")


def _start_jury(client: TestClient) -> None:
    """One team with a submitted pitch, then the jury round."""
    _login(client)
    joined = client.post("/api/teams/join", json={"name": "Team One"})
    assert joined.status_code == 200, joined.text
    assert client.post(FORWARD, json={"expectedPhase": "lobby"}).status_code == 200
    saved = client.post(
        "/api/teams/pitch",
        json={"pitch": _pitch(), "submit": True, "expectedPitchUpdatedAt": None},
    )
    assert saved.status_code == 200, saved.text
    assert client.post(FORWARD, json={"expectedPhase": "ideation"}).status_code == 200


class _FailsOneInvestorUntilHealthy:
    """Wraps the demo jury; one investor's round-1 call fails while `failing` is set."""

    name = "demo"

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.failing = True

    async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
        if self.failing and input_value.investor.id == INVESTOR_IDS[0]:
            raise RuntimeError("model unavailable")
        result: Round1Output = await self.inner.evaluate_round1(input_value)
        return result

    async def evaluate_verdict(self, input_value: Any) -> Any:
        return await self.inner.evaluate_verdict(input_value)


def test_the_server_advances_to_the_defense_by_itself_after_the_countdown() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="0.6")
    with TestClient(app) as client:
        _start_jury(client)
        _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)
        armed = _view(client)
        assert armed["session"]["phase"] == "jury"
        deadline = armed["session"]["autoAdvanceAt"]
        assert deadline - armed["session"]["serverNow"] <= 600

        # The projector and the team phones show the same deadline.
        screen = client.get("/api/screen/state").json()
        assert screen["session"]["autoAdvanceAt"] == deadline

        # Nobody polls or clicks: the server timer advances the session.
        _wait_for(lambda: app.state.runtime.store.read().phase == "defense")
        state = app.state.runtime.store.read()
        assert state.auto_advance_at is None
        assert state.phase_started_at >= deadline

        # Exactly once: later ticks, polls and timers leave the defense alone.
        started_at = state.phase_started_at
        time.sleep(0.8)
        for _ in range(3):
            _view(client)
        later = app.state.runtime.store.read()
        assert later.phase == "defense"
        assert later.phase_started_at == started_at


def test_hold_stops_the_countdown_and_keeps_the_session_in_the_jury() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="0.8")
    with TestClient(app) as client:
        _start_jury(client)
        _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)

        held = client.post(HOLD, json={"expectedPhase": "jury"})
        assert held.status_code == 200, held.text
        assert held.json()["session"]["autoAdvanceAt"] is None
        assert held.json()["session"]["autoAdvanceHeld"] is True

        time.sleep(1.4)
        view = _view(client)
        assert view["session"]["phase"] == "jury"
        assert view["session"]["autoAdvanceAt"] is None
        assert view["session"]["autoAdvanceHeld"] is True

        # Hold means manual from then on: the normal advance still works.
        advanced = client.post(FORWARD, json={"expectedPhase": "jury"})
        assert advanced.status_code == 200
        assert advanced.json()["session"]["phase"] == "defense"
        assert advanced.json()["session"]["autoAdvanceHeld"] is False


def test_hold_needs_the_moderator_a_matching_phase_and_the_jury_round() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="30")
    with TestClient(app) as client:
        assert client.post(HOLD, json={"expectedPhase": "jury"}).status_code == 401
        _login(client)
        assert client.post(HOLD, json={}).status_code == 400
        assert client.post(HOLD, json={"expectedPhase": "nope"}).status_code == 400
        # Still in the lobby: a hold for another phase is rejected without changing anything.
        stale = client.post(HOLD, json={"expectedPhase": "jury"})
        assert stale.status_code == 409
        lobby = client.post(HOLD, json={"expectedPhase": "lobby"})
        assert lobby.status_code == 409
        assert "jury" in lobby.json()["error"]
        assert app.state.runtime.store.read().auto_advance_held is False


def test_start_now_advances_at_once_and_the_timer_does_not_advance_again() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="1")
    with TestClient(app) as client:
        _start_jury(client)
        _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)

        advanced = client.post(FORWARD, json={"expectedPhase": "jury"})
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["session"]["phase"] == "defense"
        assert advanced.json()["session"]["autoAdvanceAt"] is None
        started_at = app.state.runtime.store.read().phase_started_at

        # The old deadline passes; the session must stay in the defense, not skip on.
        time.sleep(1.4)
        state = app.state.runtime.store.read()
        assert state.phase == "defense"
        assert state.phase_started_at == started_at
        # A second click of "Start now" on a stale view is rejected, not double-applied.
        assert client.post(FORWARD, json={"expectedPhase": "jury"}).status_code == 409


def test_a_failed_call_blocks_the_countdown_and_a_successful_retry_arms_it() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="30")
    runtime = app.state.runtime
    provider = _FailsOneInvestorUntilHealthy(runtime.orchestrator._provider)
    runtime.orchestrator._provider = provider
    with TestClient(app) as client:
        _start_jury(client)

        def failed() -> bool:
            team = _view(client)["teams"][0]
            return team["round1"][INVESTOR_IDS[0]]["status"] == "failed"

        _wait_for(failed)
        # The other three calls finished, yet there is no countdown.
        time.sleep(0.3)
        view = _view(client)
        assert view["teams"][0]["errorCount"] == 1
        assert view["session"]["autoAdvanceAt"] is None
        assert view["session"]["phase"] == "jury"

        provider.failing = False
        retried = client.post(
            "/api/moderator/retry", json={"teamId": view["teams"][0]["id"], "step": "round1"}
        )
        assert retried.status_code == 200, retried.text
        _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)
        assert _view(client)["session"]["phase"] == "jury"


def test_a_failed_call_never_lets_the_countdown_run_out() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="0.3")
    runtime = app.state.runtime
    runtime.orchestrator._provider = _FailsOneInvestorUntilHealthy(runtime.orchestrator._provider)
    with TestClient(app) as client:
        _start_jury(client)
        _wait_for(lambda: _view(client)["teams"][0]["errorCount"] == 1)
        time.sleep(1.0)
        view = _view(client)
        assert view["session"]["phase"] == "jury"
        assert view["session"]["autoAdvanceAt"] is None


def test_back_into_the_jury_phase_does_not_start_the_countdown() -> None:
    app = demo_app(JURY_AUTO_ADVANCE_SECONDS="0.5")
    with TestClient(app) as client:
        _start_jury(client)
        _wait_for(lambda: app.state.runtime.store.read().phase == "defense")

        back = client.post(BACK, json={"expectedPhase": "defense"})
        assert back.status_code == 200, back.text
        assert back.json()["session"]["phase"] == "jury"
        assert back.json()["session"]["autoAdvanceHeld"] is True

        # Every result is in, yet the session waits for the moderator.
        time.sleep(1.0)
        for _ in range(3):
            view = _view(client)
        assert view["session"]["phase"] == "jury"
        assert view["session"]["autoAdvanceAt"] is None
        assert client.post(FORWARD, json={"expectedPhase": "jury"}).status_code == 200


def test_the_countdown_survives_a_restart_and_a_reload() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-auto-advance-") as directory:
        path = str(Path(directory) / "session.db")

        first = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(first) as client:
            _start_jury(client)
            _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)
            deadline = _view(client)["session"]["autoAdvanceAt"]
            cookies = dict(client.cookies)

        # A reload (new page load) and a restarted server both see the same deadline.
        second = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(second, cookies=cookies) as client:
            view = _view(client)
            assert view["session"]["phase"] == "jury"
            assert view["session"]["autoAdvanceAt"] == deadline
            assert client.get("/api/screen/state").json()["session"]["autoAdvanceAt"] == deadline
            assert second.state.runtime.store.read().phase == "jury"


def test_a_countdown_that_ran_out_while_the_server_was_down_fires_on_startup() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-auto-advance-") as directory:
        path = str(Path(directory) / "session.db")

        first = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(first) as client:
            _start_jury(client)
            _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)
            first.state.runtime.store.update(
                lambda state: setattr(state, "auto_advance_at", int(time.time() * 1000) - 1)
            )

        # No browser is open: the restarted server advances the session on its own.
        second = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(second):
            _wait_for(lambda: second.state.runtime.store.read().phase == "defense", timeout_s=5)
            state = second.state.runtime.store.read()
            assert state.auto_advance_at is None
            assert state.phase == "defense"


def test_a_restart_during_a_countdown_keeps_a_hold_and_a_back() -> None:
    with tempfile.TemporaryDirectory(prefix="jury-auto-advance-") as directory:
        path = str(Path(directory) / "session.db")

        first = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(first) as client:
            _start_jury(client)
            _wait_for(lambda: _view(client)["session"]["autoAdvanceAt"] is not None)
            assert client.post(HOLD, json={"expectedPhase": "jury"}).status_code == 200
            cookies = dict(client.cookies)

        second = demo_app(SESSION_DB=path, JURY_AUTO_ADVANCE_SECONDS="60")
        with TestClient(second, cookies=cookies) as client:
            time.sleep(0.3)
            view = _view(client)
            assert view["session"]["phase"] == "jury"
            assert view["session"]["autoAdvanceAt"] is None
            assert view["session"]["autoAdvanceHeld"] is True


def test_the_countdown_defaults_to_fifteen_seconds_and_zero_turns_it_off() -> None:
    from backend.config import load_config

    assert load_config({}).jury_auto_advance_ms == 15_000
    assert load_config({"JURY_AUTO_ADVANCE_SECONDS": "30"}).jury_auto_advance_ms == 30_000
    assert load_config({"JURY_AUTO_ADVANCE_SECONDS": "0"}).jury_auto_advance_ms == 0
    assert load_config({"JURY_AUTO_ADVANCE_SECONDS": "-3"}).jury_auto_advance_ms == 15_000
    assert load_config({"JURY_AUTO_ADVANCE_SECONDS": "soon"}).jury_auto_advance_ms == 15_000
