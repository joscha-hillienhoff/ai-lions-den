"""Phase transitions and the rules attached to each new phase."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .audience import draw_pitch_order, jury_finalists
from .pitch import pitch_is_complete
from .ranking import ranked_teams, round1_started, verdict_started
from .state import (
    PhaseId,
    SessionState,
    empty_round1,
    empty_verdict,
    next_phase,
    previous_phase,
)


@dataclass(frozen=True)
class PhaseTransition:
    ok: bool
    phase: PhaseId | None = None
    error: str | None = None


def advance_phase(
    state: SessionState, now: int | None = None, rng: random.Random | None = None
) -> PhaseTransition:
    """Move the session one phase forward and apply the rules of the new phase.

    Mutates ``state`` so it can run inside a store transaction. The pitch locks
    as soon as the jury phase starts. Only submitted, complete pitches are
    evaluated; everything else is marked ineligible.

    Entering a phase is idempotent, so the moderator can step back and advance
    again without losing or repeating work: a team whose jury calls were already
    requested keeps its eligibility and results, and only teams the jury has not
    started on are evaluated afresh. The leaderboard ranking is rebuilt from the
    current verdicts, which are identical unless a verdict was retried meanwhile.
    The finalists' random pitch order is drawn once and kept for as long as it
    still matches the finalists.
    """
    target = next_phase(state.phase)
    if target is None:
        return PhaseTransition(ok=False, error="The session is already in its final phase.")

    state.phase = target
    state.phase_started_at = _now_ms() if now is None else now
    # Every phase change ends a running auto-advance countdown. Entering the jury phase
    # going forward is a fresh visit, so the countdown may arm again.
    state.auto_advance_at = None
    state.auto_advance_held = False

    if target == "jury":
        for team in state.teams:
            if round1_started(team):
                continue
            team.eligible = team.submitted_at is not None and pitch_is_complete(team.pitch)
            team.round1 = empty_round1()
            team.verdict = empty_verdict()
            team.verdict_completed_at = None

    if target == "verdict":
        for team in state.teams:
            if team.eligible is True and not verdict_started(team):
                team.verdict = empty_verdict()
                team.verdict_completed_at = None

    if target == "leaderboard":
        state.leaderboard_team_ids = [entry.team.id for entry in ranked_teams(state.teams)]
        finalist_ids = {entry.team.id for entry in jury_finalists(state)}
        if set(state.pitch_order_team_ids or []) != finalist_ids:
            state.pitch_order_team_ids = draw_pitch_order(state, rng)

    if target == "finale":
        state.finale_finalist_index = 0
        # The clock of the first finalist waits for the moderator's explicit start.
        state.finale_pitch_started_at = None

    return PhaseTransition(ok=True, phase=target)


def retreat_phase(state: SessionState, now: int | None = None) -> PhaseTransition:
    """Move the session one phase back to recover from an accidental advance.

    This only moves the phase pointer and restarts the phase timer. Teams, pitches,
    jury feedback, verdicts, votes and the finalist order stay exactly as they are;
    nothing is deleted or recomputed. Entering a phase again re-runs no completed
    work, see ``advance_phase``. The jury auto-advance stays off for the re-entered
    jury phase.
    """
    target = previous_phase(state.phase)
    if target is None:
        return PhaseTransition(ok=False, error="The session is already in its first phase.")

    state.phase = target
    state.phase_started_at = _now_ms() if now is None else now
    # Coming back into the jury phase never starts the auto-advance countdown: the
    # moderator stepped back on purpose and advances manually from here.
    state.auto_advance_at = None
    state.auto_advance_held = target == "jury"
    if target == "finale":
        # Stepping back into the finale leaves the clock not started until the moderator
        # starts it.
        state.finale_pitch_started_at = None
    return PhaseTransition(ok=True, phase=target)


def _now_ms() -> int:
    return int(time.time() * 1000)
