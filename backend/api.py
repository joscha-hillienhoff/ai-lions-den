"""HTTP API consumed by the static React user interface.

Routes, payloads, status codes and cookies are defined here as the frontend contract.
"""

from __future__ import annotations

import json
import math
import secrets
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .app import Runtime
from .archive import csv_document, export_document, export_filename
from .audience import jury_finalists
from .auth import (
    MODERATOR_COOKIE,
    TEAM_COOKIE,
    VOTER_COOKIE,
    cookie_options,
    is_moderator_token,
    moderator_token,
    password_matches,
)
from .finale import clamp_finalist_index
from .pitch import (
    pitch_is_complete,
    validate_defense,
    validate_pitch,
    validate_pitch_draft,
    validate_team_name,
)
from .qr import join_qr_data_url
from .ranking import round1_started, verdict_started
from .state import (
    PHASE_IDS,
    Defense,
    Pitch,
    Team,
    empty_round1,
    empty_verdict,
    is_at_least,
)
from .views import (
    archived_run_view,
    moderator_state_view,
    screen_state_view,
    team_state_view,
    vote_state_view,
)

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store, max-age=0"}
MAX_TEAMS = 20
SESSION_COOKIE_AGE = 60 * 60 * 12
VOTER_COOKIE_AGE = 60 * 60 * 24
VOTE_REJECTIONS = {
    "not-open": ("The audience vote has not opened yet.", 409),
    "closed": ("The audience vote is closed.", 409),
    "not-finalist": ("That team is not in the finale.", 400),
    "already-voted": ("You have already voted. Your vote is final.", 409),
}
VOTER_ID_LIMIT = 128
_MISSING = object()


def runtime_of(request: Request) -> Runtime:
    return request.app.state.runtime  # type: ignore[no-any-return]


def json_response(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(content=data, status_code=status, headers=NO_STORE)


def api_error(message: str, status: int = 400) -> JSONResponse:
    return json_response({"error": message}, status)


async def read_json(request: Request) -> dict[str, Any] | None:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - invalid JSON is a client error
        return None
    if not isinstance(body, dict):
        return None
    return body


async def read_json_any(request: Request) -> tuple[bool, Any]:
    try:
        return True, await request.json()
    except Exception:  # noqa: BLE001 - invalid JSON is a client error
        return False, None


def _safe_integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        return None
    return number if 0 <= number <= 9_007_199_254_740_991 else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _is_moderator(request: Request, runtime: Runtime) -> bool:
    return is_moderator_token(
        request.cookies.get(MODERATOR_COOKIE), runtime.config.moderator_password
    )


def _require_moderator(request: Request) -> tuple[Runtime | None, JSONResponse | None]:
    runtime = runtime_of(request)
    if not _is_moderator(request, runtime):
        return None, api_error("Moderator login required.", 401)
    return runtime, None


def _current_team(request: Request, runtime: Runtime) -> Team | None:
    token = request.cookies.get(TEAM_COOKIE)
    if not token:
        return None
    return next((team for team in runtime.store.read().teams if team.token == token), None)


def _secure_cookie(runtime: Runtime, request: Request) -> bool:
    if (runtime.config.public_base_url or "").startswith("https"):
        return True
    return request.url.scheme == "https"


def _voter_id(request: Request) -> tuple[str, bool]:
    """Return the browser's voter id and whether it had to be created."""
    value = request.cookies.get(VOTER_COOKIE)
    if isinstance(value, str) and 0 < len(value) <= VOTER_ID_LIMIT:
        return value, False
    return secrets.token_urlsafe(24), True


def _set_voter_cookie(
    response: JSONResponse, runtime: Runtime, request: Request, voter_id: str
) -> None:
    _set_cookie(
        response,
        VOTER_COOKIE,
        voter_id,
        **cookie_options(_secure_cookie(runtime, request), VOTER_COOKIE_AGE),
    )


def _set_cookie(response: JSONResponse, key: str, value: str, **options: Any) -> None:
    response.set_cookie(key, value, **options)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _download(content: str, media_type: str, filename: str) -> Response:
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _export_format(request: Request) -> str | None:
    value = request.query_params.get("format", "json").strip().lower()
    return value if value in ("json", "csv") else None


def _export_response(
    state: Any,
    vote_counts: dict[str, int],
    export_format: str,
    *,
    prefix: str,
    now: int,
    source: str = "current",
    archive_id: str | None = None,
    archived_at: int | None = None,
) -> Response:
    stamp = archived_at or now
    if export_format == "json":
        document = export_document(
            state,
            vote_counts,
            generated_at=now,
            source=source,
            archive_id=archive_id,
            archived_at=archived_at,
        )
        return _download(
            json.dumps(document, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
            export_filename(prefix, stamp, "json"),
        )
    return _download(
        csv_document(state, vote_counts),
        "text/csv; charset=utf-8",
        export_filename(prefix, stamp, "csv"),
    )


@router.get("/api/health")
async def health(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    state = runtime.store.read()
    return json_response(
        {
            "ok": True,
            "phase": state.phase,
            "teams": len(state.teams),
            "demo": runtime.config.demo,
            "revision": state.revision,
        }
    )


@router.post("/api/moderator/login")
async def moderator_login(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    if not runtime.config.moderator_password:
        return api_error("MODERATOR_PASSWORD is not configured on the server.", 503)

    ok, body = await read_json_any(request)
    if not ok:
        return api_error("Invalid request body.")
    password = body.get("password") if isinstance(body, dict) else None
    password = password if isinstance(password, str) else ""

    if not password or not password_matches(password, runtime.config.moderator_password):
        return api_error("Wrong password.", 401)

    response = json_response({"ok": True})
    token = moderator_token(runtime.config.moderator_password)
    _set_cookie(
        response,
        MODERATOR_COOKIE,
        token,
        **cookie_options(_secure_cookie(runtime, request), SESSION_COOKIE_AGE),
    )
    return response


@router.post("/api/moderator/logout")
async def moderator_logout() -> JSONResponse:
    response = json_response({"ok": True})
    _set_cookie(response, MODERATOR_COOKIE, "", **cookie_options(False, 0))
    return response


@router.get("/api/moderator/state")
async def moderator_state(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None
    runtime.orchestrator.resume()
    return json_response(
        moderator_state_view(
            runtime.store.read(),
            runtime.config,
            runtime.store.vote_counts(),
            max_teams=MAX_TEAMS,
        )
    )


@router.post("/api/moderator/phase")
async def moderator_phase(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    from .phase_transition import advance_phase

    body = await read_json(request)
    expected_phase = body.get("expectedPhase") if body else None
    if not isinstance(expected_phase, str) or expected_phase not in PHASE_IDS:
        return api_error("Send a valid expectedPhase.")

    vote_counts = runtime.store.vote_counts()

    def transition(state: Any) -> dict[str, Any]:
        # A double click, or a second moderator tab, must not skip a phase.
        if state.phase != expected_phase:
            return {
                "ok": False,
                "error": "The phase changed in another tab. Refresh before advancing.",
            }
        outcome = advance_phase(state)
        if not outcome.ok:
            return {"ok": False, "error": outcome.error}
        return {
            "ok": True,
            "phase": outcome.phase,
            "view": moderator_state_view(state, runtime.config, vote_counts, max_teams=MAX_TEAMS),
        }

    result = runtime.store.update(transition)
    if not result["ok"]:
        return api_error(str(result["error"]), 409)

    runtime.orchestrator.handle_phase_entered(result["phase"])
    return json_response(result["view"])


@router.post("/api/moderator/phase/back")
async def moderator_phase_back(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    from .phase_transition import retreat_phase

    body = await read_json(request)
    expected_phase = body.get("expectedPhase") if body else None
    if not isinstance(expected_phase, str) or expected_phase not in PHASE_IDS:
        return api_error("Send a valid expectedPhase.")

    vote_counts = runtime.store.vote_counts()

    def transition(state: Any) -> dict[str, Any]:
        # A double click, or a second moderator tab, must not step back two phases.
        if state.phase != expected_phase:
            return {
                "ok": False,
                "error": "The phase changed in another tab. Refresh before going back.",
            }
        outcome = retreat_phase(state)
        if not outcome.ok:
            return {"ok": False, "error": outcome.error}
        return {
            "ok": True,
            "view": moderator_state_view(state, runtime.config, vote_counts, max_teams=MAX_TEAMS),
        }

    result = runtime.store.update(transition)
    if not result["ok"]:
        return api_error(str(result["error"]), 409)

    # Stepping back starts no jury work: the data of every later phase stays as it is.
    return json_response(result["view"])


@router.post("/api/moderator/phase/hold")
async def moderator_phase_hold(request: Request) -> JSONResponse:
    """Stop the jury round's auto-advance countdown; the rest of this visit is manual."""
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    body = await read_json(request)
    expected_phase = body.get("expectedPhase") if body else None
    if not isinstance(expected_phase, str) or expected_phase not in PHASE_IDS:
        return api_error("Send a valid expectedPhase.")

    vote_counts = runtime.store.vote_counts()

    def hold(state: Any) -> dict[str, Any]:
        # The countdown may have just fired; then there is nothing left to hold.
        if state.phase != expected_phase:
            return {
                "ok": False,
                "error": "The phase changed in another tab. Refresh before holding.",
            }
        if state.phase != "jury":
            return {"ok": False, "error": "Only the jury round advances automatically."}
        state.auto_advance_at = None
        state.auto_advance_held = True
        return {
            "ok": True,
            "view": moderator_state_view(state, runtime.config, vote_counts, max_teams=MAX_TEAMS),
        }

    result = runtime.store.update(hold)
    if not result["ok"]:
        return api_error(str(result["error"]), 409)

    runtime.orchestrator.tick_auto_advance()
    return json_response(result["view"])


@router.post("/api/moderator/reset")
async def moderator_reset(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    body = await read_json(request)
    if body is None or body.get("confirm") != "RESET":
        return api_error("Send confirm: RESET to clear the session.")

    _state, archive_id = runtime.store.reset_with_archive()
    return json_response({"ok": True, "archivedId": archive_id})


@router.get("/api/moderator/export")
async def moderator_export(request: Request) -> Response:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    export_format = _export_format(request)
    if export_format is None:
        return api_error("Format must be json or csv.", 400)

    state, vote_counts = runtime.store.read_with_votes()
    return _export_response(
        state,
        vote_counts,
        export_format,
        prefix="run",
        now=_now_ms(),
    )


@router.get("/api/moderator/archives")
async def moderator_archives(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    return json_response(
        {
            "archives": [
                {
                    "id": archive.id,
                    "archivedAt": archive.archived_at,
                    "phase": archive.phase,
                    "teamCount": archive.team_count,
                    "winner": archive.winner,
                    "votesCast": archive.votes_cast,
                }
                for archive in runtime.store.list_archives()
            ]
        }
    )


@router.get("/api/moderator/archives/{archive_id}")
async def moderator_archive(request: Request, archive_id: str) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    run = runtime.store.read_archive(archive_id)
    if run is None:
        return api_error("That past run does not exist.", 404)
    return json_response(archived_run_view(run))


@router.get("/api/moderator/archives/{archive_id}/export")
async def moderator_archive_export(request: Request, archive_id: str) -> Response:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    export_format = _export_format(request)
    if export_format is None:
        return api_error("Format must be json or csv.", 400)

    run = runtime.store.read_archive(archive_id)
    if run is None:
        return api_error("That past run does not exist.", 404)
    return _export_response(
        run.state,
        run.vote_counts,
        export_format,
        prefix="past-run",
        now=_now_ms(),
        source="archive",
        archive_id=run.summary.id,
        archived_at=run.summary.archived_at,
    )


@router.delete("/api/moderator/archives/{archive_id}")
async def moderator_delete_archive(request: Request, archive_id: str) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    if not runtime.store.delete_archive(archive_id):
        return api_error("That past run does not exist.", 404)
    return json_response({"ok": True})


@router.post("/api/moderator/retry")
async def moderator_retry(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    body = await read_json(request)
    team_id = body.get("teamId") if body else None
    team_id = team_id if isinstance(team_id, str) else ""
    step = body.get("step") if body else None
    if not team_id:
        return api_error("Missing team id.")
    if step not in ("round1", "verdict"):
        return api_error("Step must be round1 or verdict.")

    team = next(
        (candidate for candidate in runtime.store.read().teams if candidate.id == team_id),
        None,
    )
    if team is None:
        return api_error("Team not found.", 404)
    if step == "verdict" and runtime.orchestrator.verdict_is_active(team_id):
        return api_error("A verdict run is already in progress.", 409)

    job = (
        runtime.orchestrator.retry_round1(team_id)
        if step == "round1"
        else runtime.orchestrator.retry_verdict(team_id)
    )
    runtime.orchestrator.start_background(job)

    return json_response({"ok": True, "accepted": step})


@router.post("/api/moderator/finale")
async def moderator_finale(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    body = await read_json(request)
    index = _integer(body.get("index")) if body is not None else None
    if body is None or index is None:
        return api_error("Send an integer finalist index.")

    vote_counts = runtime.store.vote_counts()

    def select(state: Any) -> dict[str, Any]:
        if state.phase != "finale":
            return {
                "ok": False,
                "error": "The finale spotlight is only available in the finale phase.",
            }
        state.finale_finalist_index = clamp_finalist_index(index, len(jury_finalists(state)))
        # Every pick restarts the 3-minute orientation clock of the spotlighted pitch.
        state.finale_pitch_started_at = _now_ms()
        return {
            "ok": True,
            "view": moderator_state_view(state, runtime.config, vote_counts, max_teams=MAX_TEAMS),
        }

    outcome = runtime.store.update(select)
    if not outcome["ok"]:
        return api_error(str(outcome["error"]), 409)
    return json_response(outcome["view"])


@router.post("/api/moderator/finale/start")
async def moderator_finale_start(request: Request) -> JSONResponse:
    runtime, error = _require_moderator(request)
    if error:
        return error
    assert runtime is not None

    vote_counts = runtime.store.vote_counts()

    def start(state: Any) -> dict[str, Any]:
        if state.phase != "finale":
            return {
                "ok": False,
                "error": "The pitch clock is only available in the finale phase.",
            }
        # Idempotent: a second click never restarts a clock that is already running.
        if state.finale_pitch_started_at is None:
            state.finale_pitch_started_at = _now_ms()
        return {
            "ok": True,
            "view": moderator_state_view(state, runtime.config, vote_counts, max_teams=MAX_TEAMS),
        }

    outcome = runtime.store.update(start)
    if not outcome["ok"]:
        return api_error(str(outcome["error"]), 409)
    return json_response(outcome["view"])


@router.get("/api/screen/state")
async def screen_state(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    runtime.orchestrator.resume()

    origin = str(request.base_url).rstrip("/")
    public_base = runtime.config.public_base_url or origin
    join_url = f"{public_base}/team"
    vote_url = f"{public_base}/vote"

    return json_response(
        screen_state_view(
            runtime.store.read(),
            join_url=join_url,
            qr_data_url=join_qr_data_url(join_url),
            join_short_url=runtime.config.join_short_url or "",
            vote_url=vote_url,
            vote_qr_data_url=join_qr_data_url(vote_url),
            vote_counts=runtime.store.vote_counts(),
            max_teams=MAX_TEAMS,
        )
    )


@router.get("/api/team/state")
async def team_state(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    runtime.orchestrator.resume()
    state = runtime.store.read()
    team = _current_team(request, runtime)
    return json_response(team_state_view(state, team, runtime.store.vote_counts()))


@router.get("/api/vote/state")
async def vote_state(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    runtime.orchestrator.resume()
    voter_id, created = _voter_id(request)
    response = json_response(
        vote_state_view(
            runtime.store.read(),
            voted_team_id=runtime.store.vote_for(voter_id),
            vote_counts=runtime.store.vote_counts(),
        )
    )
    if created:
        _set_voter_cookie(response, runtime, request, voter_id)
    return response


@router.post("/api/vote")
async def submit_vote(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    body = await read_json(request)
    team_id = body.get("teamId") if body is not None else None
    if not isinstance(team_id, str) or not team_id:
        return api_error("Choose one of the three finalists.")

    voter_id, created = _voter_id(request)

    def gate(state: Any) -> str | None:
        if state.phase != "voting":
            return "closed" if is_at_least(state.phase, "winner") else "not-open"
        if team_id not in {entry.team.id for entry in jury_finalists(state)}:
            return "not-finalist"
        return None

    rejection = runtime.store.cast_vote(voter_id, team_id, gate)
    if rejection is not None:
        message, status = VOTE_REJECTIONS[rejection]
        response = api_error(message, status)
    else:
        response = json_response({"ok": True, "votedTeamId": team_id})
    if created:
        _set_voter_cookie(response, runtime, request, voter_id)
    return response


@router.post("/api/teams/join")
async def teams_join(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    body = await read_json(request)
    existing_token = request.cookies.get(TEAM_COOKIE)
    current = runtime.store.read()
    existing = next((team for team in current.teams if team.token == existing_token), None)
    if existing is not None:
        return json_response(team_state_view(current, existing))

    name_result = validate_team_name(body.get("name") if body else None)
    if not name_result.ok:
        return api_error(name_result.error or "Choose a team name.")

    team_name = name_result.name or ""

    def register(state: Any) -> dict[str, Any]:
        if len(state.teams) >= MAX_TEAMS:
            return {"ok": False, "error": f"The session is full ({MAX_TEAMS} teams)."}
        if any(team.name.lower() == team_name.lower() for team in state.teams):
            return {"ok": False, "error": "That team name is already taken."}
        if state.phase not in ("lobby", "ideation"):
            return {
                "ok": False,
                "error": "Team registration is closed after the pitch phase.",
            }

        now = _now_ms()
        team_id = str(uuid.uuid4())
        token = secrets.token_urlsafe(24)
        state.teams.append(
            Team(
                id=team_id,
                name=team_name,
                token=token,
                created_at=now,
                pitch=Pitch(),
                round1=empty_round1(),
                defense=Defense(),
                verdict=empty_verdict(),
            )
        )
        team = next(candidate for candidate in state.teams if candidate.id == team_id)
        return {"ok": True, "token": token, "view": team_state_view(state, team)}

    outcome = runtime.store.update(register)
    if not outcome["ok"]:
        return api_error(str(outcome["error"]), 409)

    response = json_response(outcome["view"])
    _set_cookie(
        response,
        TEAM_COOKIE,
        outcome["token"],
        **cookie_options(_secure_cookie(runtime, request), SESSION_COOKIE_AGE),
    )
    return response


@router.post("/api/teams/pitch")
async def teams_pitch(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    team = _current_team(request, runtime)
    if team is None:
        return api_error("Join the session first.", 401)

    body = await read_json(request)
    if body is None:
        return api_error("Invalid request body.")

    expected_value = body.get("expectedPitchUpdatedAt", _MISSING)
    expected = _safe_integer(expected_value) if expected_value is not _MISSING else None
    if expected_value is _MISSING or (expected_value is not None and expected is None):
        return api_error("Invalid pitch revision.")

    submit = body.get("submit") is True
    raw_pitch = body.get("pitch")
    validation = validate_pitch(raw_pitch) if submit else validate_pitch_draft(raw_pitch)
    if not validation.ok:
        return json_response(
            {"error": "Check the pitch fields.", "fieldErrors": validation.errors}, 400
        )

    assert validation.pitch is not None
    if submit and not pitch_is_complete(validation.pitch):
        return api_error("Fill in every pitch field before submitting.", 400)

    updated_pitch = validation.pitch

    def save(state: Any) -> dict[str, Any]:
        if state.phase != "ideation":
            return {
                "ok": False,
                "error": "The pitch is locked. The jury phase has started.",
            }
        target = next((candidate for candidate in state.teams if candidate.id == team.id), None)
        if target is None:
            return {"ok": False, "error": "Team not found."}
        if round1_started(target):
            # The moderator stepped back after the jury already read this pitch.
            return {
                "ok": False,
                "error": "The pitch is locked. The jury has already evaluated it.",
            }
        if target.pitch_updated_at != expected:
            return {
                "ok": False,
                "error": "The pitch changed in another tab. Refresh before saving.",
            }

        updated_at = max(_now_ms(), (target.pitch_updated_at or 0) + 1)
        target.pitch = updated_pitch
        target.pitch_updated_at = updated_at
        if submit and target.submitted_at is None:
            target.submitted_at = updated_at
        return {"ok": True, "view": team_state_view(state, target)}

    outcome = runtime.store.update(save)
    if not outcome["ok"]:
        return api_error(str(outcome["error"]), 409)
    return json_response(outcome["view"])


@router.post("/api/teams/defense")
async def teams_defense(request: Request) -> JSONResponse:
    runtime = runtime_of(request)
    team = _current_team(request, runtime)
    if team is None:
        return api_error("Join the session first.", 401)

    body = await read_json(request)
    if body is None:
        return api_error("Invalid request body.")

    expected_value = body.get("expectedDefenseSubmittedAt", _MISSING)
    expected = _safe_integer(expected_value) if expected_value is not _MISSING else None
    if expected_value is _MISSING or (expected_value is not None and expected is None):
        return api_error("Invalid defense revision.")

    validation = validate_defense(body)
    if not validation.ok:
        return json_response(
            {
                "error": (
                    "Answer all four questions and describe your one change before submitting."
                ),
                "fieldErrors": validation.errors,
            },
            400,
        )

    answers = validation.answers or []
    one_change = validation.one_change or ""

    def save(state: Any) -> dict[str, Any]:
        if state.phase != "defense":
            return {"ok": False, "error": "The defense phase is closed."}
        target = next((candidate for candidate in state.teams if candidate.id == team.id), None)
        if target is None:
            return {"ok": False, "error": "Team not found."}
        if target.eligible is not True:
            return {
                "ok": False,
                "error": "Your pitch was not evaluated, so there are no questions to answer.",
            }
        if verdict_started(target):
            # The moderator stepped back after the jury already read these answers.
            return {
                "ok": False,
                "error": "The answers are locked. The final verdict has already started.",
            }
        if target.defense.submitted_at != expected:
            return {
                "ok": False,
                "error": "The answers changed in another tab. Refresh before saving.",
            }

        submitted_at = max(_now_ms(), (target.defense.submitted_at or 0) + 1)
        target.defense.answers = answers
        target.defense.one_change = one_change
        target.defense.submitted_at = submitted_at
        return {"ok": True, "view": team_state_view(state, target)}

    outcome = runtime.store.update(save)
    if not outcome["ok"]:
        return api_error(str(outcome["error"]), 409)
    return json_response(outcome["view"])
