"""Session state models and phase rules."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from .personas import INVESTOR_IDS

PhaseId = Literal[
    "lobby",
    "ideation",
    "jury",
    "defense",
    "verdict",
    "leaderboard",
    "finale",
    "voting",
    "winner",
]


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


# "minutes" is the countdown shown for a phase. None marks an untimed phase: the
# finale runs as long as the three pitches take and the winner page stays open
# until the session is reset. Neither shows a countdown or a time-is-up state.
PHASES: tuple[dict[str, object], ...] = (
    {
        "id": "lobby",
        "label": "Lobby",
        "tagline": "Take your seats. The investors are arriving.",
        "minutes": 5,
        "screen_hint": "Join the session",
    },
    {
        "id": "ideation",
        "label": "Ideation",
        "tagline": "Build the idea. Fill in the pitch.",
        "minutes": 20,
        "screen_hint": "Pitch in progress",
    },
    {
        "id": "jury",
        "label": "Jury round 1",
        "tagline": "Four investors. Four independent verdicts.",
        "minutes": 5,
        "screen_hint": "The jury is reading",
    },
    {
        "id": "defense",
        "label": "Defense",
        "tagline": "Answer the questions. The pitch stays locked.",
        "minutes": 10,
        "screen_hint": "Teams are answering",
    },
    {
        "id": "verdict",
        "label": "Final verdict",
        "tagline": "Four investors evaluate independently in parallel.",
        "minutes": 5,
        "screen_hint": "Independent verdicts are arriving",
    },
    {
        "id": "leaderboard",
        "label": "Leaderboard",
        "tagline": "The stage is set.",
        "minutes": 3,
        "screen_hint": "The final three",
    },
    {
        "id": "finale",
        "label": "Finale",
        "tagline": "The top three pitch live.",
        "minutes": None,
        "screen_hint": "Live from the stage",
    },
    {
        "id": "voting",
        "label": "Voting",
        "tagline": "Scan the code. Pick the winner.",
        "minutes": 3,
        "screen_hint": "Audience vote",
    },
    {
        "id": "winner",
        "label": "Winner",
        "tagline": "The room has decided.",
        "minutes": None,
        "screen_hint": "The reveal",
    },
)

PHASE_IDS: tuple[PhaseId, ...] = tuple(phase["id"] for phase in PHASES)  # type: ignore[misc]

JuryCallStatus = Literal["pending", "running", "done", "failed"]


def phase_index(phase: PhaseId) -> int:
    return PHASE_IDS.index(phase)


def is_at_least(phase: PhaseId, target: PhaseId) -> bool:
    return phase_index(phase) >= phase_index(target)


def next_phase(phase: PhaseId) -> PhaseId | None:
    index = PHASE_IDS.index(phase) + 1
    return PHASE_IDS[index] if index < len(PHASE_IDS) else None


def previous_phase(phase: PhaseId) -> PhaseId | None:
    index = PHASE_IDS.index(phase) - 1
    return PHASE_IDS[index] if index >= 0 else None


def phase_info(phase: PhaseId) -> dict[str, object]:
    for info in PHASES:
        if info["id"] == phase:
            return info
    raise ValueError(f"Unknown phase: {phase}")


def phase_minutes(phase: PhaseId) -> int | None:
    minutes = phase_info(phase)["minutes"]
    return None if minutes is None else int(minutes)  # type: ignore[call-overload]


# Guide length of one live finale pitch. Orientation only: nothing ever advances by itself.
FINALE_PITCH_MINUTES = 3


def finale_pitch_ends_at(state: SessionState) -> int | None:
    """The orientation deadline of the spotlighted pitch.

    None outside the finale and while the moderator has not started the clock yet.
    """
    if state.phase != "finale":
        return None
    started = state.finale_pitch_started_at
    if started is None:
        return None
    return started + FINALE_PITCH_MINUTES * 60_000


def phase_ends_at(phase: PhaseId, started_at: int) -> int | None:
    """The countdown deadline in epoch milliseconds, or None for an untimed phase."""
    minutes = phase_minutes(phase)
    return None if minutes is None else started_at + minutes * 60_000


class Round1Entry(Model):
    status: JuryCallStatus
    score: int | None = None
    assessment: str | None = None
    question: str | None = None
    error: str | None = None
    started_at: int | None = None
    finished_at: int | None = None


class VerdictEntry(Model):
    status: JuryCallStatus
    statement: str | None = None
    score: int | None = None
    decision: Literal["in", "out"] | None = None
    error: str | None = None
    started_at: int | None = None
    finished_at: int | None = None


class Pitch(Model):
    startup_name: str = ""
    tagline: str = ""
    problem: str = ""
    solution: str = ""
    customers: str = ""
    business_model: str = ""


PitchFieldKey = Literal[
    "startup_name", "tagline", "problem", "solution", "customers", "business_model"
]


class Defense(Model):
    answers: list[str] = Field(default_factory=lambda: ["", "", "", ""])
    # "Our one change": the concrete change the team makes because of the criticism.
    # Older stored sessions do not contain it and fall back to an empty string.
    one_change: str = ""
    submitted_at: int | None = None


class Team(Model):
    id: str
    name: str
    token: str
    created_at: int
    pitch: Pitch = Field(default_factory=Pitch)
    pitch_updated_at: int | None = None
    submitted_at: int | None = None
    eligible: bool | None = None
    round1: dict[str, Round1Entry] = Field(default_factory=dict)
    defense: Defense = Field(default_factory=Defense)
    verdict: dict[str, VerdictEntry] = Field(default_factory=dict)
    verdict_completed_at: int | None = None


class SessionState(Model):
    phase: PhaseId = "lobby"
    phase_started_at: int = 0
    created_at: int = 0
    leaderboard_team_ids: list[str] | None = None
    # The finalists in the random, fixed order in which they pitch. Drawn once when
    # the leaderboard starts, independent of the jury ranking.
    pitch_order_team_ids: list[str] | None = None
    finale_finalist_index: int = 0
    # Epoch ms at which the selected finalist's orientation clock started: when the
    # moderator started it or last picked a finalist. None outside the finale and while
    # the moderator has not started it yet.
    finale_pitch_started_at: int | None = None
    # The server-side deadline (epoch ms) at which the jury round advances to the defense
    # by itself, set once every eligible team's round-1 result is complete. None while
    # no countdown runs.
    auto_advance_at: int | None = None
    # True once the moderator pressed Hold, or stepped back into the jury phase: from then
    # on this visit to the jury phase only advances manually.
    auto_advance_held: bool = False
    revision: int = 0
    teams: list[Team] = Field(default_factory=list)


DEFENSE_ANSWER_LIMIT = 1000
DEFENSE_CHANGE_LIMIT = 1000
TEAM_NAME_LIMIT = 40


def empty_round1() -> dict[str, Round1Entry]:
    return {investor_id: Round1Entry(status="pending") for investor_id in INVESTOR_IDS}


def empty_verdict() -> dict[str, VerdictEntry]:
    return {investor_id: VerdictEntry(status="pending") for investor_id in INVESTOR_IDS}


def create_initial_state(now: int) -> SessionState:
    return SessionState(
        phase="lobby",
        phase_started_at=now,
        created_at=now,
        leaderboard_team_ids=None,
        pitch_order_team_ids=None,
        finale_finalist_index=0,
        revision=0,
        teams=[],
    )
