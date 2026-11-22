"""API view builders.

These shapes are the contract with the React user interface and must stay
stable: the UI was built against the previous TypeScript implementation.
"""

from __future__ import annotations

import time

from .audience import (
    AudienceResult,
    audience_results,
    jury_finalists,
    pitch_ordered_finalists,
    winner_basis,
)
from .config import AppConfig
from .finale import clamp_finalist_index
from .jury.validation import INVESTMENT_SCORE_THRESHOLD, INVESTOR_SCORE_MAX
from .personas import INVESTOR_IDS, PUBLIC_INVESTORS, InvestorId
from .pitch import pitch_is_complete
from .ranking import RankedTeam, ranked_teams_for_state, round1_started, verdict_started
from .state import (
    Pitch,
    SessionState,
    Team,
    finale_pitch_ends_at,
    is_at_least,
    phase_ends_at,
    phase_info,
)
from .store import ArchivedRunDetail


def session_view(state: SessionState) -> dict[str, object]:
    info = phase_info(state.phase)
    return {
        "phase": state.phase,
        "label": info["label"],
        "tagline": info["tagline"],
        "screenHint": info["screen_hint"],
        "minutes": info["minutes"],
        "phaseStartedAt": state.phase_started_at,
        "phaseEndsAt": phase_ends_at(state.phase, state.phase_started_at),
        # The orientation clock of the spotlighted finale pitch (about 3 minutes, never
        # enforced), or null outside the finale.
        "pitchEndsAt": finale_pitch_ends_at(state),
        # True while the finale clock has not been started by the moderator yet.
        "pitchNotStarted": state.phase == "finale" and state.finale_pitch_started_at is None,
        "serverNow": _now_ms(),
        # The server-side deadline of the jury round's automatic advance, or null.
        "autoAdvanceAt": state.auto_advance_at if state.phase == "jury" else None,
        "autoAdvanceHeld": state.auto_advance_held and state.phase == "jury",
        "revision": state.revision,
        "investmentThreshold": INVESTMENT_SCORE_THRESHOLD,
        "scoreMax": INVESTOR_SCORE_MAX,
    }


def _pitch_view(pitch: Pitch) -> dict[str, str]:
    return {
        "startupName": pitch.startup_name,
        "tagline": pitch.tagline,
        "problem": pitch.problem,
        "solution": pitch.solution,
        "customers": pitch.customers,
        "businessModel": pitch.business_model,
    }


def _ranked_view(state: SessionState) -> list[dict[str, object]]:
    finalist_ids = {entry.team.id for entry in jury_finalists(state)}
    return [
        {
            "rank": index + 1,
            "teamId": entry.team.id,
            "teamName": entry.team.name,
            "startupName": entry.team.pitch.startup_name,
            "tagline": entry.team.pitch.tagline,
            "total": entry.total,
            "inCount": entry.in_count,
            "isTopThree": entry.team.id in finalist_ids,
        }
        for index, entry in enumerate(ranked_teams_for_state(state))
    ]


def _ranked_list_view(entry: dict[str, object]) -> dict[str, object]:
    return {
        "rank": entry["rank"],
        "teamId": entry["teamId"],
        "teamName": entry["teamName"],
        "startupName": entry["startupName"],
        "total": entry["total"],
        "inCount": entry["inCount"],
    }


def _finalist_view(entry: RankedTeam, rank: int | None = None) -> dict[str, object]:
    """A finalist without jury figures; pass ``rank`` to add the moderator's figures."""
    view: dict[str, object] = {
        "teamId": entry.team.id,
        "teamName": entry.team.name,
        "startupName": entry.team.pitch.startup_name,
        "tagline": entry.team.pitch.tagline,
    }
    if rank is not None:
        view.update({"rank": rank, "total": entry.total, "inCount": entry.in_count})
    return view


def _reveal_finalist_view(result: AudienceResult, positions: dict[str, int]) -> dict[str, object]:
    """A finalist at the reveal, with both the audience and the jury result."""
    entry = result.entry
    return {
        "rank": positions[entry.team.id],
        "teamId": entry.team.id,
        "teamName": entry.team.name,
        "startupName": entry.team.pitch.startup_name,
        "tagline": entry.team.pitch.tagline,
        "total": entry.total,
        "inCount": entry.in_count,
        "audienceVotes": result.votes,
        "audienceShare": result.share,
        "isWinner": result.is_winner,
    }


def _finalist_views(
    state: SessionState, vote_counts: dict[str, int]
) -> tuple[list[dict[str, object]], str | None]:
    """The public finalists for the current phase and, at the reveal, why the winner won.

    Finalist selection follows the jury ranking. From the leaderboard until the
    reveal, every view lists the finalists in their random, fixed pitch order and
    withholds the jury figures and ranking, so the room and the audience vote
    without seeing the jury result. Only the winner phase publishes the figures.
    """
    if state.phase == "winner":
        results = audience_results(state, vote_counts)
        positions = {
            entry.team.id: index + 1 for index, entry in enumerate(ranked_teams_for_state(state))
        }
        return (
            [_reveal_finalist_view(result, positions) for result in results],
            winner_basis(results),
        )
    if is_at_least(state.phase, "leaderboard"):
        return [_finalist_view(entry) for entry in pitch_ordered_finalists(state)], None
    return [], None


def team_state_view(
    state: SessionState,
    team: Team | None,
    vote_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    counts = vote_counts or {}
    show_round1 = is_at_least(state.phase, "defense")
    show_verdict = is_at_least(state.phase, "verdict")
    # The full ranking and placement only appear at the reveal.
    reveal = state.phase == "winner"

    ranked_entries = _ranked_view(state) if reveal else []
    ranked = [_ranked_list_view(entry) for entry in ranked_entries]
    team_rank = (
        next((entry for entry in ranked if entry["teamId"] == team.id), None) if team else None
    )
    finalist_list, finalist_basis = _finalist_views(state, counts)

    return {
        "session": session_view(state),
        "investors": PUBLIC_INVESTORS,
        "team": _team_view(team, show_round1, show_verdict) if team else None,
        "ranking": ranked,
        "placement": team_rank,
        "finalists": finalist_list,
        "winnerBasis": finalist_basis,
        "votesCast": sum(counts.values()),
        "selectedFinalistIndex": clamp_finalist_index(
            state.finale_finalist_index, len(finalist_list)
        ),
    }


def _team_view(team: Team, show_round1: bool, show_verdict: bool) -> dict[str, object]:
    round1: dict[InvestorId, dict[str, object]] = {}
    for investor_id in INVESTOR_IDS:
        entry = team.round1[investor_id]
        round1[investor_id] = {
            "status": entry.status,
            "score": (entry.score if show_round1 else None),
            "assessment": (entry.assessment if show_round1 else None),
            "question": (entry.question if show_round1 else None),
            "error": (entry.error if show_round1 else None),
        }

    verdict: dict[InvestorId, dict[str, object]] = {}
    for investor_id in INVESTOR_IDS:
        entry = team.verdict[investor_id]
        verdict[investor_id] = {
            "status": entry.status,
            "statement": (entry.statement if show_verdict else None),
            "score": (entry.score if show_verdict else None),
            "previousScore": (team.round1[investor_id].score if show_verdict else None),
            "decision": (entry.decision if show_verdict else None),
            "error": (entry.error if show_verdict else None),
        }

    return {
        "id": team.id,
        "name": team.name,
        "pitch": _pitch_view(team.pitch),
        "pitchUpdatedAt": team.pitch_updated_at,
        "submittedAt": team.submitted_at,
        "pitchLocked": round1_started(team),
        "eligible": team.eligible,
        "round1": round1,
        "defense": {
            "answers": team.defense.answers,
            "oneChange": team.defense.one_change,
            "submittedAt": team.defense.submitted_at,
            "locked": verdict_started(team),
        },
        "verdict": verdict,
        "verdictCompletedAt": team.verdict_completed_at,
    }


def _count_round1_done(teams: list[Team]) -> int:
    return sum(
        1
        for team in teams
        if team.eligible is True
        and all(team.round1[investor_id].status == "done" for investor_id in INVESTOR_IDS)
    )


def screen_state_view(
    state: SessionState,
    *,
    join_url: str,
    qr_data_url: str,
    join_short_url: str = "",
    vote_url: str = "",
    vote_qr_data_url: str = "",
    vote_counts: dict[str, int] | None = None,
    max_teams: int,
) -> dict[str, object]:
    teams = state.teams
    counts = vote_counts or {}
    # The full ranking and audience result stay server-side until the reveal.
    ranked = (
        [_ranked_list_view(entry) for entry in _ranked_view(state)]
        if state.phase == "winner"
        else []
    )
    finalists, finalist_basis = _finalist_views(state, counts)

    return {
        "session": session_view(state),
        "investors": PUBLIC_INVESTORS,
        "joinUrl": join_url,
        "joinShortUrl": join_short_url,
        "qrDataUrl": qr_data_url,
        "voteUrl": vote_url,
        "voteQrDataUrl": vote_qr_data_url,
        "votesCast": sum(counts.values()),
        "maxTeams": max_teams,
        "selectedFinalistIndex": clamp_finalist_index(state.finale_finalist_index, len(finalists)),
        "counts": {
            "joined": len(teams),
            "submitted": sum(1 for team in teams if team.submitted_at is not None),
            "eligible": sum(1 for team in teams if team.eligible is True),
            "round1Done": _count_round1_done(teams),
            "defenseSubmitted": sum(
                1
                for team in teams
                if team.eligible is True and team.defense.submitted_at is not None
            ),
            "verdictDone": sum(1 for team in teams if team.verdict_completed_at is not None),
        },
        "round1ByInvestor": {
            investor_id: sum(
                1
                for team in teams
                if team.eligible is True and team.round1[investor_id].status == "done"
            )
            for investor_id in INVESTOR_IDS
        },
        "verdictByInvestor": {
            investor_id: sum(
                1
                for team in teams
                if team.eligible is True and team.verdict[investor_id].status == "done"
            )
            for investor_id in INVESTOR_IDS
        },
        "teams": [
            {
                "id": team.id,
                "name": team.name,
                "submitted": team.submitted_at is not None,
                "eligible": team.eligible is True,
                "round1Done": team.eligible is True
                and all(team.round1[investor_id].status == "done" for investor_id in INVESTOR_IDS),
                "defenseSubmitted": team.defense.submitted_at is not None,
                "verdictDone": team.verdict_completed_at is not None,
            }
            for team in teams
        ],
        "ranking": ranked,
        "finalists": finalists,
        "winnerBasis": finalist_basis,
    }


def vote_state_view(
    state: SessionState,
    *,
    voted_team_id: str | None,
    vote_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    """Public voting page state. Never includes a tally before the reveal."""
    counts = vote_counts or {}
    finalists, finalist_basis = _finalist_views(state, counts)
    return {
        "session": session_view(state),
        "open": state.phase == "voting",
        "finalists": finalists,
        "winnerBasis": finalist_basis,
        "votesCast": sum(counts.values()),
        "hasVoted": voted_team_id is not None,
        "votedTeamId": voted_team_id,
    }


def _raw_entry(entry: object) -> dict[str, object]:
    return entry.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]


def archived_run_view(run: ArchivedRunDetail) -> dict[str, object]:
    """A read-only view of one archived run for the moderator.

    Teams use the same shape as the live moderator view, so the same detail
    component renders a past run without any live-only controls.
    """
    state = run.state
    votes = run.vote_counts
    ranked = ranked_teams_for_state(state)
    ranks = {entry.team.id: index + 1 for index, entry in enumerate(ranked)}
    results = audience_results(state, votes)
    winner = results[0] if state.phase == "winner" and results else None
    ordered_teams = sorted(
        state.teams,
        key=lambda team: (ranks.get(team.id, 10_000), team.created_at),
    )

    return {
        "id": run.summary.id,
        "archivedAt": run.summary.archived_at,
        "teamCount": run.summary.team_count,
        "votesCast": run.summary.votes_cast,
        "phase": run.summary.phase,
        "session": {
            "phase": state.phase,
            "label": phase_info(state.phase)["label"],
            "createdAt": state.created_at,
            "phaseStartedAt": state.phase_started_at,
        },
        "investors": PUBLIC_INVESTORS,
        "teams": [
            {
                **_moderator_team_view(team, ranked),
                "audienceVotes": votes.get(team.id, 0),
            }
            for team in ordered_teams
        ],
        "ranking": [
            {
                "rank": index + 1,
                "teamId": entry.team.id,
                "teamName": entry.team.name,
                "startupName": entry.team.pitch.startup_name,
                "inCount": entry.in_count,
                "total": entry.total,
                "audienceVotes": votes.get(entry.team.id, 0),
            }
            for index, entry in enumerate(ranked)
        ],
        "winner": (
            {
                "teamId": winner.entry.team.id,
                "teamName": winner.entry.team.name,
                "startupName": winner.entry.team.pitch.startup_name,
                "audienceVotes": winner.votes,
                "audienceShare": winner.share,
            }
            if winner is not None
            else None
        ),
    }


def moderator_state_view(
    state: SessionState,
    config: AppConfig,
    vote_counts: dict[str, int] | None = None,
    *,
    max_teams: int,
) -> dict[str, object]:
    counts = vote_counts or {}
    ranked = ranked_teams_for_state(state)
    # The moderator spotlight must index the same order the projector shows
    # (the random, fixed pitch order), so the selection points at the same finalist in
    # both views. The moderator still sees every jury figure.
    jury_rank = {entry.team.id: index + 1 for index, entry in enumerate(ranked)}
    finale_finalists = (
        [
            _finalist_view(entry, jury_rank[entry.team.id])
            for entry in pitch_ordered_finalists(state)
        ]
        if is_at_least(state.phase, "leaderboard")
        else []
    )
    return {
        "session": session_view(state),
        "votesCast": sum(counts.values()),
        "maxTeams": max_teams,
        "mode": {
            "demo": config.demo,
            "provider": "demo" if config.demo else "openai",
            "model": "prepared demo responses" if config.demo else config.model,
            "baseUrl": config.base_url,
            "publicBaseUrl": config.public_base_url,
            "moderatorPasswordConfigured": config.moderator_password_configured,
        },
        "investors": PUBLIC_INVESTORS,
        "teams": [_moderator_team_view(team, ranked) for team in state.teams],
        "ranking": [
            {
                "rank": index + 1,
                "teamId": entry.team.id,
                "teamName": entry.team.name,
                "startupName": entry.team.pitch.startup_name,
                "inCount": entry.in_count,
                "total": entry.total,
            }
            for index, entry in enumerate(ranked)
        ],
        "finale": {
            "selectedIndex": clamp_finalist_index(
                state.finale_finalist_index, len(finale_finalists)
            ),
            "finalists": finale_finalists,
        },
    }


def _moderator_team_view(team: Team, ranked: list[RankedTeam]) -> dict[str, object]:
    ranked_entry = next((entry for entry in ranked if entry.team.id == team.id), None)
    rank = (
        next(
            (index + 1 for index, entry in enumerate(ranked) if entry.team.id == team.id),
            None,
        )
        if ranked_entry
        else None
    )
    errors = sum(
        1 for investor_id in INVESTOR_IDS if team.round1[investor_id].status == "failed"
    ) + sum(1 for investor_id in INVESTOR_IDS if team.verdict[investor_id].status == "failed")

    return {
        "id": team.id,
        "name": team.name,
        "createdAt": team.created_at,
        "pitch": _pitch_view(team.pitch),
        "submittedAt": team.submitted_at,
        "eligible": team.eligible,
        "pitchComplete": pitch_is_complete(team.pitch),
        "round1": {
            investor_id: _raw_entry(team.round1[investor_id]) for investor_id in INVESTOR_IDS
        },
        "defense": team.defense.model_dump(by_alias=True),
        "verdict": {
            investor_id: {
                **_raw_entry(team.verdict[investor_id]),
                "previousScore": team.round1[investor_id].score,
            }
            for investor_id in INVESTOR_IDS
        },
        "verdictCompletedAt": team.verdict_completed_at,
        "inCount": ranked_entry.in_count if ranked_entry else None,
        "total": ranked_entry.total if ranked_entry else None,
        "rank": rank,
        "errorCount": errors,
    }


def _now_ms() -> int:
    return int(time.time() * 1000)
