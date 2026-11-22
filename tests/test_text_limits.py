"""Pitch and defense text at the maximum lengths the app accepts.

Teams may write long pitches and defenses, so the limits must agree between the browser
and the server, and the longest accepted text must reach the jury model whole.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.benchmark.cases import DEFAULT_CASES_PATH, load_cases
from backend.benchmark.estimate import estimate_session
from backend.jury.openai import OpenAiJuryProvider
from backend.jury.pricing import MAX_JURY_PROMPT_TOKENS, estimate_prompt_tokens
from backend.jury.prompts import (
    round1_system_prompt,
    round1_user_prompt,
    verdict_system_prompt,
    verdict_user_prompt,
)
from backend.jury.types import Round1Input, Round1Summary, VerdictInput
from backend.jury.validation import ROUND1_JSON_SCHEMA, VERDICT_JSON_SCHEMA
from backend.personas import INVESTOR_IDS, investor_by_id
from backend.pitch import PITCH_FIELDS, PITCH_LIMITS_BY_KEY, validate_defense, validate_pitch
from backend.state import DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT, Pitch

from .helpers import make_config
from .test_api import _advance, _app, _join, _moderator

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_PITCH_LIMITS = {
    "startupName": 60,
    "tagline": 140,
    "problem": 2000,
    "solution": 2000,
    "customers": 1200,
    "businessModel": 2000,
}


def _text(label: str, length: int) -> str:
    """Plain words of exactly ``length`` characters that start with a unique marker."""
    words = f"{label} " * length
    return words[:length].rstrip().ljust(length, "z")


def _max_pitch() -> Pitch:
    return Pitch.model_validate(
        {
            "startup_name": _text("name", PITCH_LIMITS_BY_KEY["startupName"]),
            "tagline": _text("tagline", PITCH_LIMITS_BY_KEY["tagline"]),
            "problem": _text("problem", PITCH_LIMITS_BY_KEY["problem"]),
            "solution": _text("solution", PITCH_LIMITS_BY_KEY["solution"]),
            "customers": _text("customers", PITCH_LIMITS_BY_KEY["customers"]),
            "business_model": _text("model", PITCH_LIMITS_BY_KEY["businessModel"]),
        }
    )


def _max_payload() -> dict[str, str]:
    return _max_pitch().model_dump(by_alias=True)


def test_server_limits_match_the_defined_values() -> None:
    assert PITCH_LIMITS_BY_KEY == EXPECTED_PITCH_LIMITS
    assert DEFENSE_ANSWER_LIMIT == 1000
    assert DEFENSE_CHANGE_LIMIT == 1000


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to load the TypeScript")
def test_browser_limits_match_the_server_limits() -> None:
    """Load the real browser modules with node and compare what the form would enforce."""
    script = (
        "import { PITCH_FIELDS } from './lib/pitch.ts';"
        "import { DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT } from './lib/session-state.ts';"
        "console.log(JSON.stringify({"
        "pitch: Object.fromEntries(PITCH_FIELDS.map((field) => [field.key, field.limit])),"
        "answer: DEFENSE_ANSWER_LIMIT, change: DEFENSE_CHANGE_LIMIT }));"
    )
    result = subprocess.run(
        [
            "node",
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    browser = json.loads(result.stdout)
    assert browser["pitch"] == PITCH_LIMITS_BY_KEY
    assert browser["answer"] == DEFENSE_ANSWER_LIMIT
    assert browser["change"] == DEFENSE_CHANGE_LIMIT


@pytest.mark.parametrize("field", PITCH_FIELDS, ids=lambda field: field.key)
def test_each_pitch_field_accepts_its_maximum_and_rejects_one_more(field) -> None:
    at_limit = validate_pitch({**_max_payload()})
    assert at_limit.ok is True
    assert at_limit.pitch is not None
    assert getattr(at_limit.pitch, field.attribute) == _max_payload()[field.key]

    over = validate_pitch({**_max_payload(), field.key: "x" * (field.limit + 1)})
    assert over.ok is False
    assert str(field.limit) in (over.errors or {})[field.key]


def test_defense_accepts_the_maximum_and_rejects_one_more() -> None:
    answer = _text("answer", DEFENSE_ANSWER_LIMIT)
    change = _text("change", DEFENSE_CHANGE_LIMIT)
    at_limit = validate_defense({"answers": [answer] * 4, "oneChange": change})
    assert at_limit.ok is True
    assert at_limit.one_change == change

    long_answer = validate_defense({"answers": [answer + "x", "b", "c", "d"], "oneChange": "c"})
    assert long_answer.ok is False
    assert str(DEFENSE_ANSWER_LIMIT) in (long_answer.errors or {})[0]

    long_change = validate_defense({"answers": ["a", "b", "c", "d"], "oneChange": change + "x"})
    assert long_change.ok is False
    assert str(DEFENSE_CHANGE_LIMIT) in (long_change.errors or {})["oneChange"]


def test_maximum_length_pitch_and_defense_are_stored_and_returned_whole() -> None:
    app = _app()
    moderator = _moderator(TestClient(app.app))
    _join(app, "Long Team")
    _advance(moderator, "ideation")

    payload = _max_payload()
    saved = app.post(
        "/api/teams/pitch",
        json={"pitch": payload, "submit": True, "expectedPitchUpdatedAt": None},
    )
    assert saved.status_code == 200, saved.text
    assert app.get("/api/team/state").json()["team"]["pitch"] == payload

    _advance(moderator, "jury")
    _advance(moderator, "defense")
    answers = [_text(f"answer{index}", DEFENSE_ANSWER_LIMIT) for index in range(4)]
    change = _text("change", DEFENSE_CHANGE_LIMIT)
    defended = app.post(
        "/api/teams/defense",
        json={"answers": answers, "oneChange": change, "expectedDefenseSubmittedAt": None},
    )
    assert defended.status_code == 200, defended.text

    team = app.get("/api/team/state").json()["team"]
    assert team["defense"]["answers"] == answers
    assert team["defense"]["oneChange"] == change
    detail = moderator.get("/api/moderator/state").json()["teams"][0]
    assert detail["pitch"] == payload
    assert detail["defense"]["answers"] == answers
    assert detail["defense"]["oneChange"] == change


def _verdict_input(pitch: Pitch, investor_id: str) -> VerdictInput:
    investor = investor_by_id(investor_id)
    return VerdictInput(
        team_id="team-1",
        team_name="x" * 40,
        pitch=pitch,
        investor=investor,
        answer=_text("answer", DEFENSE_ANSWER_LIMIT),
        one_change=_text("change", DEFENSE_CHANGE_LIMIT),
        round1=[
            Round1Summary(
                investor_id=other,
                investor_name=investor_by_id(other).name,
                score=5,
                assessment=_text("assessment", 900),
                question=_text("question", 500),
            )
            for other in INVESTOR_IDS
        ],
        previous=[],
    )


def test_jury_prompts_carry_the_maximum_length_texts_whole() -> None:
    pitch = _max_pitch()
    round1 = round1_user_prompt(team_name="Team", pitch=pitch)
    verdict = verdict_user_prompt(_verdict_input(pitch, INVESTOR_IDS[0]))

    for prompt in (round1, verdict):
        assert pitch.problem in prompt
        assert pitch.solution in prompt
        assert pitch.customers in prompt
        assert pitch.business_model in prompt
    assert _text("answer", DEFENSE_ANSWER_LIMIT) in verdict
    assert _text("change", DEFENSE_CHANGE_LIMIT) in verdict


async def test_benchmark_guard_lets_maximum_length_prompts_through_uncut() -> None:
    requests: list[dict] = []

    class Completions:
        async def create(self, **arguments):
            requests.append(arguments)
            name = arguments["response_format"]["json_schema"]["name"]
            content = (
                '{"score": 7, "assessment": "Solid.", "question": "Why now?"}'
                if name == "jury_round1"
                else '{"score": 8, "statement": "Concrete."}'
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, content=content))],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=10),
            )

    provider = OpenAiJuryProvider(
        make_config(api_key="test-key", demo=False), benchmark_limits=True
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    pitch = _max_pitch()
    investor = investor_by_id(INVESTOR_IDS[0])
    await provider.evaluate_round1(Round1Input("team-1", "x" * 40, pitch, investor))
    await provider.evaluate_verdict(_verdict_input(pitch, INVESTOR_IDS[0]))

    assert len(requests) == 2
    assert pitch.problem in requests[0]["messages"][1]["content"]
    verdict_prompt = requests[1]["messages"][1]["content"]
    assert pitch.business_model in verdict_prompt
    assert _text("answer", DEFENSE_ANSWER_LIMIT) in verdict_prompt
    assert _text("change", DEFENSE_CHANGE_LIMIT) in verdict_prompt


def test_prompt_cap_covers_the_longest_ascii_prompts() -> None:
    """The preflight guard sees system prompt, user prompt and schema, like this estimate."""
    pitch = _max_pitch()
    for investor_id in INVESTOR_IDS:
        investor = investor_by_id(investor_id)
        round1 = (
            round1_system_prompt(investor)
            + "\n"
            + round1_user_prompt(team_name="x" * 40, pitch=pitch)
            + json.dumps(ROUND1_JSON_SCHEMA, separators=(",", ":"))
        )
        verdict = (
            verdict_system_prompt(investor)
            + "\n"
            + verdict_user_prompt(_verdict_input(pitch, investor_id))
            + json.dumps(VERDICT_JSON_SCHEMA, separators=(",", ":"))
        )
        assert estimate_prompt_tokens(round1) <= MAX_JURY_PROMPT_TOKENS
        assert estimate_prompt_tokens(verdict) <= MAX_JURY_PROMPT_TOKENS


def test_benchmark_estimate_counts_the_full_pitch_text() -> None:
    case = load_cases(DEFAULT_CASES_PATH)[0]
    variant = case.variants[0]
    short, _ = estimate_session(case, variant)

    long_pitch = replace(
        case.pitch,
        problem=_text("problem", PITCH_LIMITS_BY_KEY["problem"]),
        solution=_text("solution", PITCH_LIMITS_BY_KEY["solution"]),
        customers=_text("customers", PITCH_LIMITS_BY_KEY["customers"]),
        business_model=_text("model", PITCH_LIMITS_BY_KEY["businessModel"]),
    )
    long_case = replace(case, pitch=long_pitch)
    long, _ = estimate_session(long_case, variant)
    assert long > short
