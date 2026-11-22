"""Finalist order and the audience reveal.

The three finalists are selected by the same deals-first jury ranking used
everywhere else. Before the reveal, every view lists them in one random, fixed
pitch order that is drawn independently of the ranking. The audience result is
only computed in the Winner phase.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from .ranking import RankedTeam, ranked_teams_for_state, select_finalists
from .state import SessionState

FINALIST_COUNT = 3

# Why the first audience result won, mirroring the order in `audience_results`.
WINNER_BASIS_AUDIENCE_VOTES = "audienceVotes"
WINNER_BASIS_JURY_IN_COUNT = "juryInCount"
WINNER_BASIS_JURY_TOTAL = "juryTotal"
WINNER_BASIS_JURY_RANKING = "juryRanking"


@dataclass(frozen=True)
class AudienceResult:
    entry: RankedTeam
    votes: int
    share: float
    is_winner: bool


def jury_finalists(state: SessionState) -> list[RankedTeam]:
    """The top three of the one deals-first ranking, in that order."""
    return select_finalists(ranked_teams_for_state(state), FINALIST_COUNT)


def draw_pitch_order(state: SessionState, rng: random.Random | None = None) -> list[str]:
    """Draw the random pitch order of the finalists, as team ids.

    The draw is a uniform shuffle that ignores the jury ranking, so the order
    reveals nothing about who ranked first. It runs once, when the leaderboard
    phase fixes the finalists; the result is stored on the session.
    """
    team_ids = [entry.team.id for entry in jury_finalists(state)]
    (rng or random.SystemRandom()).shuffle(team_ids)
    return team_ids


def pitch_ordered_finalists(state: SessionState) -> list[RankedTeam]:
    """The finalists in their stored pitch order: the one order every pre-reveal view shares.

    A session that reached the leaderboard before the pitch order existed, or
    whose stored order does not match its finalists, falls back to team
    registration order, which is also independent of the ranking.
    """
    finalists = jury_finalists(state)
    by_team_id = {entry.team.id: entry for entry in finalists}
    stored = state.pitch_order_team_ids
    if stored is not None and len(stored) == len(by_team_id) and set(stored) == set(by_team_id):
        return [by_team_id[team_id] for team_id in stored]
    registration_order = {team.id: index for index, team in enumerate(state.teams)}
    return sorted(finalists, key=lambda entry: registration_order[entry.team.id])


def audience_results(state: SessionState, vote_counts: Mapping[str, int]) -> list[AudienceResult]:
    """Rank the finalists for the reveal.

    The audience decides: most votes first. On a vote tie the better jury
    ranking position decides, which means more "I'm in" decisions first and
    then more points. With no votes at all the jury ranking decides, so a round
    with zero votes still renders sensibly.
    """
    finalists = jury_finalists(state)
    if not finalists:
        return []

    positions = {entry.team.id: index for index, entry in enumerate(ranked_teams_for_state(state))}
    total = sum(max(0, int(vote_counts.get(entry.team.id, 0))) for entry in finalists)

    rows = [
        AudienceResult(
            entry=entry,
            votes=max(0, int(vote_counts.get(entry.team.id, 0))),
            share=(
                round(max(0, int(vote_counts.get(entry.team.id, 0))) * 100 / total, 1)
                if total
                else 0.0
            ),
            is_winner=False,
        )
        for entry in finalists
    ]
    rows.sort(key=lambda row: (-row.votes, positions[row.entry.team.id]))
    return [replace(row, is_winner=index == 0) for index, row in enumerate(rows)]


def winner_basis(results: Sequence[AudienceResult]) -> str | None:
    """Explain the winner using the same order `audience_results` sorted by.

    The most audience votes win. On a vote tie the better jury ranking position
    decides: first the number of "I'm in" decisions, then the jury total, then
    the earlier pitch submission. With no votes at all the jury ranking decides.
    """
    if not results:
        return None
    top = results[0]
    if top.votes == 0:
        return WINNER_BASIS_JURY_RANKING
    if len(results) == 1 or top.votes > results[1].votes:
        return WINNER_BASIS_AUDIENCE_VOTES
    other = results[1].entry
    if top.entry.in_count > other.in_count:
        return WINNER_BASIS_JURY_IN_COUNT
    if top.entry.total > other.total:
        return WINNER_BASIS_JURY_TOTAL
    return WINNER_BASIS_JURY_RANKING
