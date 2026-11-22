"""Pitch form definitions, validation and normalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .state import DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT, TEAM_NAME_LIMIT, Pitch


@dataclass(frozen=True)
class PitchField:
    key: str
    attribute: str
    label: str
    hint: str
    limit: int
    multiline: bool
    placeholder: str


PITCH_FIELDS: tuple[PitchField, ...] = (
    PitchField(
        key="startupName",
        attribute="startup_name",
        label="Startup name",
        hint="Make it memorable.",
        limit=60,
        multiline=False,
        placeholder="e.g. Loopline AI",
    ),
    PitchField(
        key="tagline",
        attribute="tagline",
        label="One-line tagline",
        hint="The idea in one breath.",
        limit=140,
        multiline=False,
        placeholder="e.g. The AI that turns online fashion returns into local resale.",
    ),
    PitchField(
        key="problem",
        attribute="problem",
        label="Problem",
        hint="Who suffers, how often, and what does it cost them today?",
        limit=2000,
        multiline=True,
        placeholder="Describe the concrete pain you are solving.",
    ),
    PitchField(
        key="solution",
        attribute="solution",
        label="Solution",
        hint="What exactly do you build, and why is it meaningfully better?",
        limit=2000,
        multiline=True,
        placeholder="Describe the AI product and its unfair advantage.",
    ),
    PitchField(
        key="customers",
        attribute="customers",
        label="Target customers",
        hint="Be specific. A narrow customer beats everyone.",
        limit=1200,
        multiline=True,
        placeholder="Who pays, who uses, and how many of them are there?",
    ),
    PitchField(
        key="businessModel",
        attribute="business_model",
        label="Business model",
        hint="How money comes in and why the numbers can work.",
        limit=2000,
        multiline=True,
        placeholder="Pricing, revenue model, unit economics.",
    ),
)

PITCH_LIMITS_BY_KEY: dict[str, int] = {field.key: field.limit for field in PITCH_FIELDS}

_CONTROL_CHARACTERS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


def clean_text(value: str) -> str:
    return _CONTROL_CHARACTERS.sub("", value.replace("\r\n", "\n").replace("\r", "\n")).strip()


@dataclass(frozen=True)
class PitchValidation:
    ok: bool
    pitch: Pitch | None = None
    errors: dict[str, str] | None = None


def _source_object(input_value: Any) -> dict[str, Any]:
    return input_value if isinstance(input_value, dict) else {}


def _collect_pitch(input_value: Any, *, require_complete: bool) -> PitchValidation:
    source = _source_object(input_value)
    pitch = Pitch()
    errors: dict[str, str] = {}

    for field in PITCH_FIELDS:
        raw = source.get(field.key)
        value = clean_text(raw) if isinstance(raw, str) else ""
        if not value:
            if require_complete:
                errors[field.key] = f"{field.label} is required."
            else:
                setattr(pitch, field.attribute, value)
            continue
        if len(value) > field.limit:
            errors[field.key] = f"{field.label} must be at most {field.limit} characters."
            continue
        setattr(pitch, field.attribute, value)

    if errors:
        return PitchValidation(ok=False, errors=errors)
    return PitchValidation(ok=True, pitch=pitch)


def validate_pitch(input_value: Any) -> PitchValidation:
    return _collect_pitch(input_value, require_complete=True)


def validate_pitch_draft(input_value: Any) -> PitchValidation:
    """Drafts may be empty, but no field may exceed its limit."""
    return _collect_pitch(input_value, require_complete=False)


def pitch_is_complete(pitch: Pitch) -> bool:
    for field in PITCH_FIELDS:
        value = getattr(pitch, field.attribute) or ""
        if not value.strip() or len(value) > field.limit:
            return False
    return True


@dataclass(frozen=True)
class DefenseValidation:
    ok: bool
    answers: list[str] | None = None
    one_change: str | None = None
    errors: dict[int | str, str] | None = None


def validate_defense(input_value: Any) -> DefenseValidation:
    source = _source_object(input_value)
    raw = source.get("answers")
    values = raw if isinstance(raw, list) else []
    answers = [
        clean_text(values[index]) if index < len(values) and isinstance(values[index], str) else ""
        for index in range(4)
    ]
    raw_change = source.get("oneChange")
    one_change = clean_text(raw_change) if isinstance(raw_change, str) else ""

    errors: dict[int | str, str] = {}
    for index, answer in enumerate(answers):
        if not answer:
            errors[index] = "An answer is required."
        elif len(answer) > DEFENSE_ANSWER_LIMIT:
            errors[index] = f"Answers must be at most {DEFENSE_ANSWER_LIMIT} characters."
    if not one_change:
        errors["oneChange"] = "Describe the one change you make because of the criticism."
    elif len(one_change) > DEFENSE_CHANGE_LIMIT:
        errors["oneChange"] = f"Our one change must be at most {DEFENSE_CHANGE_LIMIT} characters."

    if errors:
        return DefenseValidation(ok=False, errors=errors)
    return DefenseValidation(ok=True, answers=answers, one_change=one_change)


@dataclass(frozen=True)
class TeamNameValidation:
    ok: bool
    name: str | None = None
    error: str | None = None


def validate_team_name(input_value: Any) -> TeamNameValidation:
    name = ""
    if isinstance(input_value, str):
        name = re.sub(r"\s+", " ", clean_text(input_value))
    if not name:
        return TeamNameValidation(ok=False, error="Choose a team name.")
    if len(name) > TEAM_NAME_LIMIT:
        return TeamNameValidation(
            ok=False,
            error=f"Team names are limited to {TEAM_NAME_LIMIT} characters.",
        )
    return TeamNameValidation(ok=True, name=name)
