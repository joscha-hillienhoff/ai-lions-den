from __future__ import annotations

import asyncio
import re
from dataclasses import fields
from types import SimpleNamespace

import pytest

from backend.config import load_config
from backend.jury.demo import DemoJuryProvider
from backend.jury.openai import OpenAiJuryProvider
from backend.jury.orchestrator import JuryOrchestrator
from backend.jury.types import (
    Round1Input,
    Round1Output,
    Round1Summary,
    VerdictInput,
    VerdictOutput,
)
from backend.jury.validation import clamp_verdict_score, parse_verdict_output
from backend.personas import INVESTOR_IDS, investor_by_id
from backend.phase_transition import advance_phase
from backend.ranking import ranked_teams
from backend.state import Round1Entry, SessionState

from .helpers import make_config, make_state, make_team, new_store, wait_for, write_state

EXPECTED_INVESTOR_ORDER = ("growth", "finance", "customer", "technology")


def _verdict_complete(state: SessionState) -> bool:
    return all(
        team.verdict[investor_id].status == "done"
        for team in state.teams
        for investor_id in EXPECTED_INVESTOR_ORDER
    )


def _round1_complete(state: SessionState) -> bool:
    return all(
        team.round1[investor_id].status == "done"
        for team in state.teams
        for investor_id in EXPECTED_INVESTOR_ORDER
    )


async def test_runs_round_one_for_twenty_teams_and_all_verdicts() -> None:
    store = new_store()
    teams = [make_team(index + 1) for index in range(20)]
    write_state(store, make_state(teams, "jury"))
    orchestrator = JuryOrchestrator(store, DemoJuryProvider(1), make_config(max_concurrency=6))

    orchestrator.handle_phase_entered("jury")
    await wait_for(lambda: _round1_complete(store.read()), 20_000)

    state = store.read()
    assert _round1_complete(state) is True
    assert len(state.teams) == 20
    for team in state.teams:
        for investor_id in INVESTOR_IDS:
            entry = team.round1[investor_id]
            assert entry.status == "done"
            assert 1 <= (entry.score or 0) <= 10
            assert len(entry.assessment or "") > 40
            assert "?" in (entry.question or "")

    def to_verdict(current: SessionState) -> None:
        assert advance_phase(current).ok is True
        assert advance_phase(current).ok is True

    store.update(to_verdict)

    orchestrator.handle_phase_entered("verdict")
    await wait_for(lambda: _verdict_complete(store.read()), 20_000)

    state = store.read()
    assert _verdict_complete(state) is True
    ranking = ranked_teams(state.teams)
    assert len(ranking) == 20
    assert len({entry.total for entry in ranking}) > 1
    for team in state.teams:
        assert team.verdict_completed_at is not None
        for investor_id in INVESTOR_IDS:
            entry = team.verdict[investor_id]
            assert len(entry.statement or "") > 20
            assert entry.decision in ("in", "out")
            assert (entry.score or 0) >= 1


async def test_respects_the_concurrency_limit() -> None:
    active = 0
    max_active = 0
    verdict_calls: dict[str, list[str]] = {}

    class TrackingProvider:
        name = "demo"

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.008)
            active -= 1
            return Round1Output(
                score=7,
                assessment="A solid pitch with a clear risk.",
                question="Why now?",
            )

        async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.005)
            active -= 1
            verdict_calls.setdefault(input_value.team_id, []).append(input_value.investor.id)
            return VerdictOutput(
                statement=(
                    "Your answer addresses the main risk, and your one change makes it testable. "
                    "My score held at 7."
                ),
                score=7,
            )

    store = new_store()
    teams = [make_team(index + 1) for index in range(6)]
    write_state(store, make_state(teams, "jury"))
    orchestrator = JuryOrchestrator(store, TrackingProvider(), make_config(max_concurrency=3))

    orchestrator.handle_phase_entered("jury")
    await wait_for(lambda: _round1_complete(store.read()), 20_000)
    assert max_active <= 3

    def to_verdict(current: SessionState) -> None:
        advance_phase(current)
        advance_phase(current)

    store.update(to_verdict)
    verdict_calls.clear()
    orchestrator.handle_phase_entered("verdict")
    await wait_for(lambda: _verdict_complete(store.read()), 20_000)

    assert max_active <= 3
    for team in store.read().teams:
        assert sorted(verdict_calls.get(team.id, [])) == sorted(EXPECTED_INVESTOR_ORDER)


async def test_runs_all_four_verdict_calls_concurrently() -> None:
    active = 0
    max_active = 0

    class ConcurrentProvider:
        name = "demo"

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            raise AssertionError("round 1 must not run during a verdict")

        async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.02)
            active -= 1
            return VerdictOutput(statement="A concise verdict in my own words.", score=6)

    store = new_store()
    team = make_team(1)
    for score, investor_id in enumerate(EXPECTED_INVESTOR_ORDER, start=4):
        team.round1[investor_id] = Round1Entry(
            status="done",
            score=score,
            assessment="An independent assessment.",
            question="An independent question?",
        )
    write_state(store, make_state([team], "verdict"))
    orchestrator = JuryOrchestrator(store, ConcurrentProvider(), make_config(max_concurrency=8))

    await orchestrator.run_verdict(team.id)

    assert max_active == len(EXPECTED_INVESTOR_ORDER)
    assert all(
        store.read().teams[0].verdict[investor_id].status == "done"
        for investor_id in EXPECTED_INVESTOR_ORDER
    )


async def test_a_failed_verdict_does_not_stop_the_other_investors_and_retry_resets_all() -> None:
    calls: dict[str, int] = {}
    failed_once = False

    class FlakyVerdictProvider:
        name = "demo"

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            raise AssertionError("round 1 must not run during a verdict")

        async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
            nonlocal failed_once
            calls[input_value.investor.id] = calls.get(input_value.investor.id, 0) + 1
            if input_value.investor.id == "finance" and not failed_once:
                failed_once = True
                raise RuntimeError("simulated verdict failure")
            return VerdictOutput(statement="A concise verdict in my own words.", score=6)

    store = new_store()
    team = make_team(1)
    for score, investor_id in enumerate(EXPECTED_INVESTOR_ORDER, start=4):
        team.round1[investor_id] = Round1Entry(
            status="done",
            score=score,
            assessment="An independent assessment.",
            question="An independent question?",
        )
    write_state(store, make_state([team], "verdict"))
    orchestrator = JuryOrchestrator(store, FlakyVerdictProvider(), make_config(max_concurrency=8))

    await orchestrator.run_verdict(team.id)

    state = store.read()
    assert state.teams[0].verdict["finance"].status == "failed"
    assert "simulated verdict failure" in (state.teams[0].verdict["finance"].error or "")
    for investor_id in ("growth", "customer", "technology"):
        assert state.teams[0].verdict[investor_id].status == "done"
    assert state.teams[0].verdict_completed_at is None
    assert calls == {"growth": 1, "finance": 1, "customer": 1, "technology": 1}

    await orchestrator.retry_verdict(team.id)

    state = store.read()
    assert all(
        state.teams[0].verdict[investor_id].status == "done"
        for investor_id in EXPECTED_INVESTOR_ORDER
    )
    assert state.teams[0].verdict_completed_at is not None
    assert calls == {"growth": 2, "finance": 2, "customer": 2, "technology": 2}


async def test_isolates_a_failed_call_and_retries_only_that_team() -> None:
    class FlakyProvider(DemoJuryProvider):
        def __init__(self) -> None:
            super().__init__(1)
            self.failed = False

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            if (
                input_value.team_id == "team-2"
                and input_value.investor.id == "growth"
                and not self.failed
            ):
                self.failed = True
                raise RuntimeError("simulated model failure")
            return await super().evaluate_round1(input_value)

    store = new_store()
    teams = [make_team(index + 1) for index in range(3)]
    write_state(store, make_state(teams, "jury"))
    orchestrator = JuryOrchestrator(store, FlakyProvider(), make_config())

    orchestrator.handle_phase_entered("jury")
    await wait_for(
        lambda: all(
            team.round1[investor_id].status in ("done", "failed")
            for team in store.read().teams
            for investor_id in EXPECTED_INVESTOR_ORDER
        )
    )

    state = store.read()
    broken_team = next(team for team in state.teams if team.id == "team-2")
    assert broken_team.round1["growth"].status == "failed"
    assert "simulated model failure" in (broken_team.round1["growth"].error or "")
    for team in state.teams:
        if team.id != "team-2":
            assert all(
                team.round1[investor_id].status == "done" for investor_id in EXPECTED_INVESTOR_ORDER
            )
    healthy_scores = [team.round1["finance"].score for team in state.teams if team.id != "team-2"]

    await orchestrator.retry_round1("team-2")
    await wait_for(
        lambda: (
            next(team for team in store.read().teams if team.id == "team-2").round1["growth"].status
            == "done"
        )
    )

    state = store.read()
    assert (
        next(team for team in state.teams if team.id == "team-2").round1["growth"].status == "done"
    )
    assert [
        team.round1["finance"].score for team in state.teams if team.id != "team-2"
    ] == healthy_scores


async def test_recovers_a_step_left_running_by_a_restart() -> None:
    store = new_store()
    team = make_team(1)
    team.round1["growth"] = Round1Entry(status="running", started_at=123)
    write_state(store, make_state([team], "jury"))
    orchestrator = JuryOrchestrator(store, DemoJuryProvider(1), make_config())

    orchestrator.resume()
    await wait_for(lambda: _round1_complete(store.read()), 10_000)
    assert store.read().teams[0].round1["growth"].status == "done"


def test_has_four_distinct_personas_each_with_the_required_configuration() -> None:
    expected_roles = {
        "growth": "Growth investor",
        "finance": "Finance investor",
        "customer": "Customer investor",
        "technology": "Technology investor",
    }
    assert set(INVESTOR_IDS) == set(expected_roles)
    assert len(INVESTOR_IDS) == len(expected_roles)

    investors = [investor_by_id(investor_id) for investor_id in INVESTOR_IDS]
    assert {investor.id: investor.role for investor in investors} == expected_roles
    for investor in investors:
        assert len(investor.name) > 3
        assert len(investor.focus) > 3
        assert len(investor.intro) > 3
        assert len(investor.avatar) > 0
        assert len(investor.perspective) > 10
        assert len(investor.lens) > 20
        assert len(investor.personality) > 20
        assert len(investor.speaking_style) > 20


async def test_demo_sentences_start_with_capitals_including_first_speaker() -> None:
    provider = DemoJuryProvider(0)
    for index in range(60):
        team = make_team(index)
        for investor_id in INVESTOR_IDS:
            investor = investor_by_id(investor_id)
            round1 = await provider.evaluate_round1(
                Round1Input(team.id, team.name, team.pitch, investor)
            )
            verdict = await provider.evaluate_verdict(
                VerdictInput(team.id, team.name, team.pitch, investor, "", "", [], [])
            )
            for text in (round1.assessment, round1.question, verdict.statement):
                for sentence in re.split(r"[.!?]\s+", text):
                    assert sentence[0].isupper(), sentence


def test_verdict_parser_defers_final_score_bounds_to_the_post_model_boundary() -> None:
    output = parse_verdict_output(
        {
            "statement": (
                "Your answer addresses the risk, and your one change tests it. My score rose."
            ),
            "score": 15,
            "decision": "in",
        }
    )
    assert output.score == 15


def test_clamps_final_scores_to_two_points_and_the_one_to_ten_range() -> None:
    assert clamp_verdict_score(10, 5) == 7
    assert clamp_verdict_score(2, 5) == 3
    assert clamp_verdict_score(5, 5) == 5
    assert clamp_verdict_score(10, 1) == 3
    assert clamp_verdict_score(1, 10) == 8
    assert clamp_verdict_score(99, 4) == 6
    assert clamp_verdict_score(-4, 4) == 2
    assert clamp_verdict_score(99, None) == 10
    assert clamp_verdict_score(-4, None) == 1


async def test_orchestrator_clamps_verdict_output_after_the_model() -> None:
    class OutOfRangeProvider:
        name = "demo"

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            return Round1Output(
                score=5,
                assessment="A clear pitch with one open risk.",
                question="Why now?",
            )

        async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
            scores = {"growth": 15, "finance": -5, "customer": 99, "technology": 1}
            return VerdictOutput(
                statement=(
                    "Your answer addresses the question, and your one change responds to the risk. "
                    "My score movement reflects those points."
                ),
                score=scores[input_value.investor.id],
            )

    store = new_store()
    team = make_team(1)
    for investor_id, score in (("growth", 5), ("finance", 1), ("customer", 10), ("technology", 4)):
        team.round1[investor_id] = Round1Entry(
            status="done",
            score=score,
            assessment="Assessment.",
            question="Question?",
        )
    write_state(store, make_state([team], "verdict"))
    orchestrator = JuryOrchestrator(store, OutOfRangeProvider(), make_config())

    await orchestrator.run_verdict(team.id)

    state = store.read()
    scores = {entry_id: state.teams[0].verdict[entry_id].score for entry_id in INVESTOR_IDS}
    assert scores == {"growth": 7, "finance": 1, "customer": 10, "technology": 2}
    decisions = {entry_id: state.teams[0].verdict[entry_id].decision for entry_id in INVESTOR_IDS}
    assert decisions == {
        "growth": "in",
        "finance": "out",
        "customer": "in",
        "technology": "out",
    }


async def test_a_clamped_score_decides_instead_of_the_raw_model_score() -> None:
    class RawScoreProvider:
        name = "demo"

        async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
            return Round1Output(
                score=5,
                assessment="A clear pitch with one open risk.",
                question="Why now?",
            )

        async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
            return VerdictOutput(
                statement=(
                    "Your answer addresses the question, and your one change responds to the risk. "
                    "My score movement reflects those points."
                ),
                score=99,
            )

    store = new_store()
    team = make_team(1)
    for investor_id, score in (("growth", 4), ("finance", 5), ("customer", 4), ("technology", 5)):
        team.round1[investor_id] = Round1Entry(
            status="done",
            score=score,
            assessment="Assessment.",
            question="Question?",
        )
    write_state(store, make_state([team], "verdict"))
    orchestrator = JuryOrchestrator(store, RawScoreProvider(), make_config())

    await orchestrator.run_verdict(team.id)

    state = store.read()
    scores = {entry_id: state.teams[0].verdict[entry_id].score for entry_id in INVESTOR_IDS}
    decisions = {entry_id: state.teams[0].verdict[entry_id].decision for entry_id in INVESTOR_IDS}
    assert scores == {"growth": 6, "finance": 7, "customer": 6, "technology": 7}
    assert decisions == {
        "growth": "out",
        "finance": "in",
        "customer": "out",
        "technology": "in",
    }


def test_verdict_output_has_no_decision_field() -> None:
    assert "decision" not in {field.name for field in fields(VerdictOutput)}


async def test_demo_verdict_uses_the_derived_decision_for_its_statement_pool() -> None:
    provider = DemoJuryProvider(0)
    scores: set[int] = set()
    statements: dict[int, set[str]] = {}
    for index in range(80):
        team = make_team(index + 1)
        round1 = [
            Round1Summary(
                investor_id=investor_id,
                investor_name=investor_by_id(investor_id).name,
                score=2 + ((index + position) % 7),
                assessment="An earlier assessment.",
                question="An earlier question?",
            )
            for position, investor_id in enumerate(INVESTOR_IDS)
        ]
        for _position, investor_id in enumerate(INVESTOR_IDS):
            verdict = await provider.evaluate_verdict(
                VerdictInput(
                    team_id=team.id,
                    team_name=team.name,
                    pitch=team.pitch,
                    investor=investor_by_id(investor_id),
                    answer="An answer long enough to read.",
                    one_change="A concrete change.",
                    round1=round1,
                    previous=[],
                )
            )
            scores.add(verdict.score)
            statements.setdefault(verdict.score, set()).add(verdict.statement)
            expected = "I am in" if verdict.score >= 7 else "I am out"
            assert expected in verdict.statement
    assert 6 in scores
    assert 7 in scores
    assert all("I am out" in statement for statement in statements[6])
    assert all("I am in" in statement for statement in statements[7])


async def test_demo_verdict_moves_within_two_points_and_cites_the_answer_and_change() -> None:
    provider = DemoJuryProvider(0)
    answers = [
        (
            "We tested this with twelve pharmacies and they already pay for a worse "
            'workaround. It is repeatable."'
        ),
        "The first twenty stores fund the next stage through a per-store fee with a short payback.",
        "The buyer is the owner who feels the loss every month, reached through their wholesaler.",
        "The advantage is the purchasing history per store, which a copycat cannot reproduce.",
    ]
    one_change = (
        "We narrow the first customer to independent pharmacies and charge a fee per store. "
        'We test the fee weekly."'
    )

    for index in range(12):
        team = make_team(index + 1)
        round1 = [
            Round1Summary(
                investor_id=investor_id,
                investor_name=investor_by_id(investor_id).name,
                score=2 + ((index + position) % 7),
                assessment="An earlier assessment.",
                question="An earlier question?",
            )
            for position, investor_id in enumerate(INVESTOR_IDS)
        ]
        for position, investor_id in enumerate(INVESTOR_IDS):
            investor = investor_by_id(investor_id)
            input_value = VerdictInput(
                team_id=team.id,
                team_name=team.name,
                pitch=team.pitch,
                investor=investor,
                answer=answers[position],
                one_change=one_change,
                round1=round1,
                previous=[],
            )
            verdict = await provider.evaluate_verdict(input_value)
            assert 1 <= verdict.score <= 10
            assert abs(verdict.score - round1[position].score) <= 2
            assert "Your answer" in verdict.statement
            assert "one change" in verdict.statement
            assert answers[position][:24] in verdict.statement
            assert one_change[:24] in verdict.statement
            sentence_count = len(re.findall(r"[.!?]+(?=\s|$|[\"'”’)\]}])", verdict.statement))
            assert 2 <= sentence_count <= 4
            repeated = await provider.evaluate_verdict(input_value)
            assert repeated == verdict


def _openai_provider(base_url: str) -> OpenAiJuryProvider:
    config = load_config(
        {
            "OPENAI_API_KEY": "sk-test",
            "DEMO_MODE": "false",
            "OPENAI_BASE_URL": base_url,
        }
    )
    return OpenAiJuryProvider(config)


def test_uses_the_public_openai_api_when_the_base_url_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    provider = _openai_provider("")
    assert str(provider._client.base_url) == "https://api.openai.com/v1/"


def test_uses_a_configured_azure_base_url(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    provider = _openai_provider("https://example.openai.azure.com/openai/v1/")
    assert str(provider._client.base_url) == "https://example.openai.azure.com/openai/v1/"


async def test_openai_provider_sends_the_session_contract_and_own_lens_to_the_model() -> None:
    class CapturingCompletions:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def create(self, **arguments):
            self.calls.append(dict(arguments))
            schema_name = arguments["response_format"]["json_schema"]["name"]
            content = (
                '{"score": 7, "assessment": "A solid pitch with one open risk.", '
                '"question": "Why now?"}'
                if schema_name == "jury_round1"
                else (
                    '{"score": 7, "statement": "The answer is concrete and the change is '
                    'meaningful."}'
                )
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, content=content))]
            )

    provider = _openai_provider("https://example.openai.azure.com/openai/v1/")
    completions = CapturingCompletions()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    round1 = [
        Round1Summary(
            investor_id=investor_id,
            investor_name=investor_by_id(investor_id).name,
            score=4 + position,
            assessment=f"assessment-marker-{investor_id}",
            question=f"question-marker-{investor_id}?",
        )
        for position, investor_id in enumerate(INVESTOR_IDS)
    ]
    leaked_statement = "statement-marker-from-an-earlier-investor"

    for investor_id in INVESTOR_IDS:
        investor = investor_by_id(investor_id)
        await provider.evaluate_round1(
            Round1Input("team-1", "Team 1", make_team(1).pitch, investor)
        )
        await provider.evaluate_verdict(
            VerdictInput(
                team_id="team-1",
                team_name="Team 1",
                pitch=make_team(1).pitch,
                investor=investor,
                answer=f"answer-marker-{investor_id}",
                one_change="change-marker",
                round1=round1,
                previous=[
                    {
                        "investorId": other,
                        "investorName": investor_by_id(other).name,
                        "statement": leaked_statement,
                        "score": 9,
                        "decision": "in",
                    }
                    for other in INVESTOR_IDS
                    if other != investor_id
                ],
            )
        )

    assert len(completions.calls) == len(INVESTOR_IDS) * 2
    expected_role_domains = {
        "growth": ("market size", "growth path", "scalability"),
        "finance": ("pricing", "unit economics", "financial viability"),
        "customer": ("target customer", "problem pain", "willingness to pay"),
        "technology": ("technical feasibility", "ai accuracy", "defensibility"),
    }
    for position, investor_id in enumerate(INVESTOR_IDS):
        investor = investor_by_id(investor_id)
        round1_call = completions.calls[position * 2]
        verdict_call = completions.calls[position * 2 + 1]
        for call in (round1_call, verdict_call):
            system_prompt = call["messages"][0]["content"]
            assert investor.name in system_prompt
            assert investor.lens in system_prompt
            assert "other investors" in system_prompt
            assert "when a defense answer is available" in system_prompt
            assert all(
                domain in system_prompt.lower() for domain in expected_role_domains[investor_id]
            )
            for other in INVESTOR_IDS:
                if other == investor_id:
                    continue
                assert investor_by_id(other).name not in system_prompt
                assert investor_by_id(other).lens not in system_prompt

        round1_user = round1_call["messages"][1]["content"]
        assert "exactly one question" in round1_user
        assert "about 25 words" in round1_user
        assert "no second part joined" in round1_user
        assert "about 40 words" in round1_user
        assert "500 characters" in round1_user

        verdict_user = verdict_call["messages"][1]["content"]
        assert f"answer-marker-{investor_id}" in verdict_user
        assert f"assessment-marker-{investor_id}" in verdict_user
        assert f"question-marker-{investor_id}" in verdict_user
        assert "change-marker" in verdict_user
        assert "about 40 words" in verdict_user
        assert "2 to 3" in verdict_user
        assert "move your score up" in verdict_user
        assert "If no one change was submitted" in verdict_user
        assert "7 or higher" in verdict_user
        assert '"decision"' not in verdict_user
        assert leaked_statement not in verdict_user
        for other in INVESTOR_IDS:
            if other == investor_id:
                continue
            assert f"answer-marker-{other}" not in verdict_user
            assert f"assessment-marker-{other}" not in verdict_user
            assert f"question-marker-{other}" not in verdict_user

    for position in range(len(INVESTOR_IDS)):
        round1_call = completions.calls[position * 2]
        verdict_call = completions.calls[position * 2 + 1]
        for call in (round1_call, verdict_call):
            system_prompt = call["messages"][0]["content"]
            assert "Scoring rubric for this startup pitch event" in system_prompt
            assert "Do not penalise the absence of real customers" in system_prompt
            assert "Assumptions, estimates and a concrete plan are valid evidence" in system_prompt
            assert "7-8 strong" in system_prompt
            assert "Use the full range and be discriminating" in system_prompt

        round1_schema = round1_call["response_format"]["json_schema"]["schema"]
        assert round1_schema["additionalProperties"] is False
        assert round1_schema["required"] == ["score", "assessment", "question"]
        assert set(round1_schema["properties"]) == {"score", "assessment", "question"}
        assert "40 words" in round1_schema["properties"]["assessment"]["description"]
        assert "25 words" in round1_schema["properties"]["question"]["description"]
        assert "500 characters" in round1_schema["properties"]["question"]["description"]

        verdict_schema = verdict_call["response_format"]["json_schema"]["schema"]
        assert verdict_schema["additionalProperties"] is False
        assert verdict_schema["required"] == ["statement", "score"]
        assert set(verdict_schema["properties"]) == {"statement", "score"}
        assert "40 words" in verdict_schema["properties"]["statement"]["description"]
        assert "own voice" in verdict_schema["properties"]["statement"]["description"]
        assert "decision" not in verdict_schema["properties"]


class _TemperatureRejection(Exception):
    status_code = 400

    def __init__(self) -> None:
        super().__init__(
            "Error code: 400 - Unsupported value: 'temperature' does not support 0.4 "
            "with this model. Only the default (1) value is supported."
        )
        self.message = str(self)


class _TemperatureAwareCompletions:
    """A fake model client that rejects every request carrying temperature."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **arguments):
        self.calls.append(dict(arguments))
        # Overlap the concurrent requests so they all send temperature before the
        # first rejection flips the provider's shared fallback flag.
        await asyncio.sleep(0.01)
        if "temperature" in arguments:
            raise _TemperatureRejection()
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        refusal=None,
                        content=(
                            '{"score": 7, "assessment": "A solid pitch with one open '
                            'risk.", "question": "Why now?"}'
                        ),
                    )
                )
            ]
        )


async def test_every_concurrent_call_retries_without_temperature() -> None:
    provider = _openai_provider("https://example.openai.azure.com/openai/v1/")
    completions = _TemperatureAwareCompletions()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    results = await asyncio.gather(
        *(
            provider.evaluate_round1(
                Round1Input(
                    team_id=f"team-{index}",
                    team_name=f"Team {index}",
                    pitch=make_team(index + 1).pitch,
                    investor=investor_by_id(investor_id),
                )
            )
            for index, investor_id in enumerate(INVESTOR_IDS)
        )
    )

    assert [result.score for result in results] == [7, 7, 7, 7]
    assert sum("temperature" in call for call in completions.calls) == len(INVESTOR_IDS)
    assert sum("temperature" not in call for call in completions.calls) == len(INVESTOR_IDS)


async def test_a_temperature_rejected_retry_does_not_loop() -> None:
    class AlwaysRejecting:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **arguments):
            self.calls += 1
            raise _TemperatureRejection()

    provider = _openai_provider("https://example.openai.azure.com/openai/v1/")
    completions = AlwaysRejecting()
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(_TemperatureRejection):
        await provider.evaluate_round1(
            Round1Input(
                team_id="team-1",
                team_name="Team 1",
                pitch=make_team(1).pitch,
                investor=investor_by_id("growth"),
            )
        )

    # One request with temperature, one retry without it, and then give up.
    assert completions.calls == 2
