"""API-level model fault injection.

The real OpenAI provider runs against a fake model client, so a timeout, an
overload (429) and a schema-violating response can be produced deterministically
without a network call. The session has to stay usable, the moderator has to
see the failure, and Retry has to recover the team.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest
from httpx import AsyncClient

from backend.config import load_config
from backend.jury.openai import OpenAiJuryProvider
from backend.personas import INVESTOR_IDS, investor_by_id

from .helpers import COMPLETE_PITCH, api_client, api_clients, demo_app, wait_for_async

PASSWORD = "test-password"


class _OverloadedError(Exception):
    status_code = 429

    def __init__(self) -> None:
        super().__init__("The model is overloaded (429).")


def _completion(content: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, content=content))]
    )


class _FakeModel:
    """A model client that fails one investor of one team until it is healthy."""

    def __init__(self, failure: str) -> None:
        self.failure = failure
        self.healthy = False
        self.calls = 0
        self.targeted_calls = 0

    async def create(self, **arguments: Any) -> Any:
        self.calls += 1
        schema = arguments["response_format"]["json_schema"]["name"]
        system = arguments["messages"][0]["content"]
        user = arguments["messages"][1]["content"]
        investor = next(
            (
                investor_by_id(investor_id).name
                for investor_id in INVESTOR_IDS
                if investor_by_id(investor_id).name in system
            ),
            "",
        )
        targeted = "Team One" in user and not self.healthy

        if (
            targeted
            and self.failure in {"timeout", "overload", "broken"}
            and schema == "jury_round1"
            and investor == investor_by_id("growth").name
        ):
            self.targeted_calls += 1
            if self.failure == "timeout":
                raise openai.APITimeoutError(request=httpx.Request("POST", "http://model.invalid"))
            if self.failure == "overload":
                raise _OverloadedError()
            return _completion('{"score": "excellent", "assessment": "A", "question": "Q?"}')

        if (
            targeted
            and self.failure == "verdict-broken"
            and schema == "jury_verdict"
            and investor == investor_by_id("finance").name
        ):
            self.targeted_calls += 1
            return _completion('{"score": 7, "statement": ""}')

        if schema == "jury_round1":
            return _completion(
                '{"score": 7, "assessment": "A concrete assessment with one clear risk.", '
                '"question": "Which customer will pay first?"}'
            )
        return _completion(
            '{"score": 7, "statement": "The answer addresses the risk and the change is testable."}'
        )


def _faulty_provider(failure: str) -> tuple[OpenAiJuryProvider, _FakeModel]:
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "DEMO_MODE": "false",
            "OPENAI_MODEL": "test-model",
        }
    )
    provider = OpenAiJuryProvider(config)
    model = _FakeModel(failure)
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=model))
    return provider, model


async def _state(client: AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path)
    assert response.status_code == 200, (path, response.text)
    return response.json()


async def _advance(moderator: AsyncClient, expected: str) -> None:
    current = (await _state(moderator, "/api/moderator/state"))["session"]["phase"]
    response = await moderator.post("/api/moderator/phase", json={"expectedPhase": current})
    assert response.status_code == 200, response.text
    assert response.json()["session"]["phase"] == expected


def _pitch(index: int) -> dict[str, str]:
    return COMPLETE_PITCH.model_dump(by_alias=True) | {"startupName": f"Startup {index}"}


async def _join(client: AsyncClient, name: str) -> str:
    response = await client.post("/api/teams/join", json={"name": name})
    assert response.status_code == 200, response.text
    return str(response.json()["team"]["id"])


async def _submit_pitch(client: AsyncClient, index: int) -> None:
    response = await client.post(
        "/api/teams/pitch",
        json={
            "pitch": _pitch(index),
            "submit": True,
            "expectedPitchUpdatedAt": None,
        },
    )
    assert response.status_code == 200, response.text


async def _submit_defense(client: AsyncClient, index: int) -> None:
    response = await client.post(
        "/api/teams/defense",
        json={
            "answers": [
                f"Answer {index} one is concrete and measured.",
                f"Answer {index} two names a price and a cost.",
                f"Answer {index} three cites a pilot result.",
                f"Answer {index} four names the next milestone.",
            ],
            "oneChange": f"Team {index} narrows the first customer segment.",
            "expectedDefenseSubmittedAt": None,
        },
    )
    assert response.status_code == 200, response.text


def _team(view: dict[str, Any], name: str) -> dict[str, Any]:
    return next(team for team in view["teams"] if team["name"] == name)


def _all_round1_done(view: dict[str, Any]) -> bool:
    return all(
        all(team["round1"][investor_id]["status"] == "done" for investor_id in INVESTOR_IDS)
        for team in view["teams"]
    )


def _all_verdicts_done(view: dict[str, Any]) -> bool:
    return all(team["verdictCompletedAt"] is not None for team in view["teams"])


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        ("timeout", "Request timed out."),
        ("overload", "429"),
        ("broken", "Invalid round 1 output"),
    ],
)
async def test_a_round_one_model_failure_is_visible_and_retry_recovers(
    failure: str,
    expected_error: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep() -> None:
        return None

    monkeypatch.setattr(
        "backend.jury.openai.asyncio",
        SimpleNamespace(sleep=lambda _seconds: no_sleep()),
    )

    app = demo_app()
    provider, model = _faulty_provider(failure)
    app.state.runtime.orchestrator._provider = provider

    async with api_clients(app, 4) as clients:
        moderator, screen, team_one, team_two = clients
        login = await moderator.post("/api/moderator/login", json={"password": PASSWORD})
        assert login.status_code == 200, login.text

        team_one_id = await _join(team_one, "Team One")
        await _join(team_two, "Team Two")
        await _advance(moderator, "ideation")
        await asyncio.gather(_submit_pitch(team_one, 1), _submit_pitch(team_two, 2))
        await _advance(moderator, "jury")

        async def failed() -> bool:
            view = await _state(moderator, "/api/moderator/state")
            return _team(view, "Team One")["round1"]["growth"]["status"] == "failed"

        await wait_for_async(failed, timeout_ms=15_000)
        view = await _state(moderator, "/api/moderator/state")
        broken = _team(view, "Team One")
        healthy = _team(view, "Team Two")

        assert broken["round1"]["growth"]["status"] == "failed"
        assert expected_error in broken["round1"]["growth"]["error"]
        assert broken["errorCount"] >= 1
        for investor_id in INVESTOR_IDS:
            if investor_id == "growth":
                continue
            assert broken["round1"][investor_id]["status"] == "done"
        assert _all_round1_done({"teams": [healthy]})
        assert healthy["errorCount"] == 0
        assert model.targeted_calls >= 1

        # The session stays usable while that one call is broken.
        screen_state = await _state(screen, "/api/screen/state")
        assert screen_state["round1ByInvestor"]["growth"] == 1
        assert screen_state["round1ByInvestor"]["finance"] == 2
        assert (await _state(team_one, "/api/team/state"))["team"]["name"] == "Team One"
        vote_state = await _state(screen, "/api/vote/state")
        assert vote_state["open"] is False

        model.healthy = True
        retry = await moderator.post(
            "/api/moderator/retry",
            json={"teamId": team_one_id, "step": "round1"},
        )
        assert retry.status_code == 200, retry.text
        assert retry.json() == {"ok": True, "accepted": "round1"}

        async def recovered() -> bool:
            current = await _state(moderator, "/api/moderator/state")
            return _all_round1_done(current)

        await wait_for_async(recovered, timeout_ms=15_000)
        view = await _state(moderator, "/api/moderator/state")
        assert _team(view, "Team One")["round1"]["growth"]["status"] == "done"
        assert _team(view, "Team One")["errorCount"] == 0

        # The recovered session continues through defense and the final verdict.
        await _advance(moderator, "defense")
        await asyncio.gather(_submit_defense(team_one, 1), _submit_defense(team_two, 2))
        await _advance(moderator, "verdict")

        async def verdicts_done() -> bool:
            return _all_verdicts_done(await _state(moderator, "/api/moderator/state"))

        await wait_for_async(verdicts_done, timeout_ms=15_000)
        view = await _state(moderator, "/api/moderator/state")
        for team in view["teams"]:
            assert team["verdictCompletedAt"] is not None
            assert all(
                team["verdict"][investor_id]["status"] == "done" for investor_id in INVESTOR_IDS
            )


async def test_a_verdict_model_failure_is_visible_and_retry_rebuilds_all_four(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep() -> None:
        return None

    monkeypatch.setattr(
        "backend.jury.openai.asyncio",
        SimpleNamespace(sleep=lambda _seconds: no_sleep()),
    )

    app = demo_app()
    provider, model = _faulty_provider("verdict-broken")
    app.state.runtime.orchestrator._provider = provider

    async with api_clients(app, 3) as clients:
        moderator, screen, team_one = clients
        login = await moderator.post("/api/moderator/login", json={"password": PASSWORD})
        assert login.status_code == 200, login.text

        team_one_id = await _join(team_one, "Team One")
        async with api_client(app) as team_two:
            await _join(team_two, "Team Two")
            await _advance(moderator, "ideation")
            await asyncio.gather(_submit_pitch(team_one, 1), _submit_pitch(team_two, 2))
            await _advance(moderator, "jury")

            async def round_one_done() -> bool:
                return _all_round1_done(await _state(moderator, "/api/moderator/state"))

            await wait_for_async(round_one_done, timeout_ms=15_000)
            await _advance(moderator, "defense")
            await asyncio.gather(_submit_defense(team_one, 1), _submit_defense(team_two, 2))
            await _advance(moderator, "verdict")

            async def verdict_failed() -> bool:
                view = await _state(moderator, "/api/moderator/state")
                return _team(view, "Team One")["verdict"]["finance"]["status"] == "failed"

            await wait_for_async(verdict_failed, timeout_ms=15_000)
            view = await _state(moderator, "/api/moderator/state")
            broken = _team(view, "Team One")
            healthy = _team(view, "Team Two")

            assert "Invalid verdict output" in broken["verdict"]["finance"]["error"]
            assert broken["errorCount"] >= 1
            for investor_id in INVESTOR_IDS:
                if investor_id == "finance":
                    continue
                assert broken["verdict"][investor_id]["status"] == "done"
            assert healthy["verdictCompletedAt"] is not None
            assert broken["verdictCompletedAt"] is None

            screen_state = await _state(screen, "/api/screen/state")
            assert screen_state["counts"]["verdictDone"] == 1
            assert (await _state(team_one, "/api/team/state"))["team"]["name"] == "Team One"

            model.healthy = True
            retry = await moderator.post(
                "/api/moderator/retry",
                json={"teamId": team_one_id, "step": "verdict"},
            )
            assert retry.status_code == 200, retry.text
            assert retry.json() == {"ok": True, "accepted": "verdict"}

            async def recovered() -> bool:
                return _all_verdicts_done(await _state(moderator, "/api/moderator/state"))

            await wait_for_async(recovered, timeout_ms=15_000)
            view = await _state(moderator, "/api/moderator/state")
            fixed = _team(view, "Team One")
            assert fixed["verdictCompletedAt"] is not None
            for investor_id in INVESTOR_IDS:
                entry = fixed["verdict"][investor_id]
                assert entry["status"] == "done"
                assert entry["previousScore"] == fixed["round1"][investor_id]["score"]
            assert fixed["errorCount"] == 0
