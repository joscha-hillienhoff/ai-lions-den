"""Automatic advance from the jury round to the defense.

Once every eligible team's round-1 result is complete, the session counts down and
then moves to the defense by itself, so the room does not wait for the moderator.
The countdown is server-authoritative: its deadline lives in the session state, so
it survives reloads and restarts, and the server performs the transition itself.

Only the jury round advances automatically. A failed jury call keeps the countdown
from starting, so no team enters the defense without its questions. The moderator
can hold the countdown, which makes the rest of that visit manual, and stepping back
into the jury phase never starts it again.
"""

from __future__ import annotations

from typing import Literal

from .phase_transition import advance_phase
from .ranking import round1_is_complete
from .state import SessionState

AutoAdvanceAction = Literal["arm", "clear", "fire"]


def jury_round_is_complete(state: SessionState) -> bool:
    """Every eligible team has all four round-1 results; no call is pending or failed.

    With no eligible team there is nothing to wait for, so the round is not complete
    and the session never advances by itself.
    """
    eligible = [team for team in state.teams if team.eligible is True]
    return bool(eligible) and all(round1_is_complete(team) for team in eligible)


def auto_advance_action(state: SessionState, now: int, delay_ms: int) -> AutoAdvanceAction | None:
    """What has to change in the state now, or None when it is already right."""
    if state.phase != "jury" or delay_ms <= 0:
        return "clear" if state.auto_advance_at is not None else None
    if state.auto_advance_held or not jury_round_is_complete(state):
        return "clear" if state.auto_advance_at is not None else None
    if state.auto_advance_at is None:
        return "arm"
    return "fire" if now >= state.auto_advance_at else None


def apply_auto_advance(state: SessionState, now: int, delay_ms: int) -> bool:
    """Arm, clear or fire the countdown inside a store transaction.

    Returns True when this call moved the session to the defense. The check and the
    transition share one transaction, so a countdown that expires together with a
    manual advance, or in a second tab, advances exactly once.
    """
    action = auto_advance_action(state, now, delay_ms)
    if action == "arm":
        state.auto_advance_at = now + delay_ms
    elif action == "clear":
        state.auto_advance_at = None
    elif action == "fire":
        return advance_phase(state, now).ok
    return False
