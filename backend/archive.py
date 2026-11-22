"""Complete run exports and archive snapshots.

A run leaves the app in two ways: a moderator-triggered download (JSON or CSV)
and the automatic snapshot that Reset writes to SQLite before it clears the
session. Both use the shapes defined here so an exported file and an archived
run always agree.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .audience import audience_results
from .personas import INVESTOR_IDS, PUBLIC_INVESTORS
from .ranking import ranked_teams_for_state
from .state import SessionState, Team

SCHEMA_VERSION = "1.0"
RUN_KIND = "ai-lions-den-run"
CSV_BOM = "\ufeff"


def iso_timestamp(value: int | None) -> str | None:
    """Epoch milliseconds as a sortable UTC timestamp, or None."""
    if value is None:
        return None
    moment = datetime.fromtimestamp(value / 1000, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def votes_by_team(vote_counts: Mapping[str, int]) -> dict[str, int]:
    return {str(team_id): max(0, int(count)) for team_id, count in vote_counts.items()}


def winner_name(state: SessionState, vote_counts: Mapping[str, int]) -> str | None:
    """The winning team for a finished run, or None before the reveal."""
    if state.phase != "winner":
        return None
    results = audience_results(state, vote_counts)
    return results[0].entry.team.name if results else None


def export_document(
    state: SessionState,
    vote_counts: Mapping[str, int],
    *,
    generated_at: int,
    source: str = "current",
    archive_id: str | None = None,
    archived_at: int | None = None,
) -> dict[str, Any]:
    """The complete, machine-readable record of one run."""
    votes = votes_by_team(vote_counts)
    ranked = ranked_teams_for_state(state)
    ranks = {entry.team.id: index + 1 for index, entry in enumerate(ranked)}
    totals = {entry.team.id: entry for entry in ranked}

    results = audience_results(state, vote_counts)
    audience = [
        {
            "rank": index + 1,
            "teamId": result.entry.team.id,
            "teamName": result.entry.team.name,
            "startupName": result.entry.team.pitch.startup_name,
            "votes": result.votes,
            "share": result.share,
            "isWinner": state.phase == "winner" and result.is_winner,
        }
        for index, result in enumerate(results)
    ]
    reveal_winner = audience[0] if state.phase == "winner" and audience else None

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RUN_KIND,
        "generatedAt": iso_timestamp(generated_at),
        "source": {
            "kind": source,
            "archiveId": archive_id,
            "archivedAt": iso_timestamp(archived_at),
        },
        "session": {
            "phase": state.phase,
            "createdAt": iso_timestamp(state.created_at),
            "phaseStartedAt": iso_timestamp(state.phase_started_at),
            "revision": state.revision,
            "teams": len(state.teams),
            "votesCast": sum(votes.values()),
        },
        "investors": [
            {
                "id": investor_id,
                "name": investor["name"],
                "focus": investor["focus"],
            }
            for investor_id, investor in zip(INVESTOR_IDS, PUBLIC_INVESTORS, strict=True)
        ],
        "teams": [
            _team_document(
                team,
                rank=ranks.get(team.id),
                total=totals[team.id].total if team.id in totals else None,
                deals=totals[team.id].in_count if team.id in totals else None,
                audience_votes=votes.get(team.id, 0),
            )
            for team in state.teams
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
        "audience": audience,
        "winner": reveal_winner,
    }


def _team_document(
    team: Team,
    *,
    rank: int | None,
    total: int | None,
    deals: int | None,
    audience_votes: int,
) -> dict[str, Any]:
    return {
        "id": team.id,
        "name": team.name,
        "createdAt": iso_timestamp(team.created_at),
        "submittedAt": iso_timestamp(team.submitted_at),
        "eligible": team.eligible,
        "pitch": {
            "startupName": team.pitch.startup_name,
            "tagline": team.pitch.tagline,
            "problem": team.pitch.problem,
            "solution": team.pitch.solution,
            "customers": team.pitch.customers,
            "businessModel": team.pitch.business_model,
        },
        "round1": {
            investor_id: {
                "status": team.round1[investor_id].status,
                "score": team.round1[investor_id].score,
                "assessment": team.round1[investor_id].assessment,
                "question": team.round1[investor_id].question,
            }
            for investor_id in INVESTOR_IDS
        },
        "defense": {
            "answers": {
                investor_id: team.defense.answers[index]
                if index < len(team.defense.answers)
                else ""
                for index, investor_id in enumerate(INVESTOR_IDS)
            },
            "oneChange": team.defense.one_change,
            "submittedAt": iso_timestamp(team.defense.submitted_at),
        },
        "verdict": {
            investor_id: {
                "status": team.verdict[investor_id].status,
                "score": team.verdict[investor_id].score,
                "previousScore": team.round1[investor_id].score,
                "decision": team.verdict[investor_id].decision,
                "statement": team.verdict[investor_id].statement,
            }
            for investor_id in INVESTOR_IDS
        },
        "rank": rank,
        "total": total,
        "deals": deals,
        "audienceVotes": audience_votes,
    }


def csv_document(state: SessionState, vote_counts: Mapping[str, int]) -> str:
    """One row per team, ready for Excel with umlauts and multi-line text.

    The string starts with a UTF-8 byte order mark so Excel reads umlauts
    correctly when the file is opened by double click, and every field goes
    through the csv module so multi-line answers stay inside one quoted cell.
    """
    votes = votes_by_team(vote_counts)
    ranked = ranked_teams_for_state(state)
    ranks = {entry.team.id: index + 1 for index, entry in enumerate(ranked)}
    totals = {entry.team.id: entry for entry in ranked}
    rows = sorted(
        state.teams,
        key=lambda team: (ranks.get(team.id, 10_000), team.created_at, team.name),
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(_csv_header())
    for team in rows:
        writer.writerow(
            _csv_row(
                team,
                rank=ranks.get(team.id),
                total=totals[team.id].total if team.id in totals else None,
                deals=totals[team.id].in_count if team.id in totals else None,
                audience_votes=votes.get(team.id, 0),
            )
        )
    return CSV_BOM + buffer.getvalue()


def _csv_header() -> list[str]:
    header = [
        "Rank",
        "Team",
        "Startup",
        "Tagline",
        "Problem",
        "Solution",
        "Customers",
        "Business model",
        "Pitch submitted at",
        "Eligible",
    ]
    for investor in PUBLIC_INVESTORS:
        header.append(f"Answer - {investor['name']}")
    header.append("Our one change")
    for investor in PUBLIC_INVESTORS:
        name = investor["name"]
        header.extend(
            [
                f"Round 1 assessment - {name}",
                f"Round 1 question - {name}",
                f"Round 1 score - {name}",
                f"Final score - {name}",
                f"Decision - {name}",
                f"Verdict statement - {name}",
            ]
        )
    header.extend(["Final total", "Deals (I'm in)", "Audience votes"])
    return header


def _csv_row(
    team: Team,
    *,
    rank: int | None,
    total: int | None,
    deals: int | None,
    audience_votes: int,
) -> list[str | int | None]:
    row: list[str | int | None] = [
        rank,
        team.name,
        team.pitch.startup_name,
        team.pitch.tagline,
        team.pitch.problem,
        team.pitch.solution,
        team.pitch.customers,
        team.pitch.business_model,
        iso_timestamp(team.submitted_at),
        "yes" if team.eligible is True else "no" if team.eligible is False else "",
    ]
    for index, _investor_id in enumerate(INVESTOR_IDS):
        row.append(team.defense.answers[index] if index < len(team.defense.answers) else "")
    row.append(team.defense.one_change)
    for investor_id in INVESTOR_IDS:
        round1 = team.round1[investor_id]
        verdict = team.verdict[investor_id]
        row.extend(
            [
                round1.assessment or "",
                round1.question or "",
                round1.score,
                verdict.score,
                verdict.decision or "",
                verdict.statement or "",
            ]
        )
    row.extend([total, deals, audience_votes])
    return [_csv_safe_cell(value) for value in row]


def _csv_safe_cell(value: str | int | None) -> str | int | None:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_filename(prefix: str, stamp_ms: int, extension: str) -> str:
    """A Windows-safe download name, for example ai-lions-den-run-...json."""
    moment = datetime.fromtimestamp(stamp_ms / 1000, tz=UTC)
    stamp = moment.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"ai-lions-den-{prefix}-{stamp}.{extension}"
