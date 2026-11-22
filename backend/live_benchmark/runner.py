"""Play one scripted session against a deployed instance, purely over its HTTP API.

The flow mirrors a real session: the moderator archives and resets the old session,
the teams join and submit their pitches, the moderator advances to the jury, the teams
answer the lions' questions, the moderator advances to the verdict and finally to the
leaderboard. Nothing else is touched: no finale, no voting, no winner.

Every phase change sends ``expectedPhase`` like the browser does, so a double click, the
jury round's automatic advance to the defense or a second moderator tab can never skip
a phase. A failed jury step is retried at most once per team and step; the calls of
every retry are counted. A timeout or an unrecoverable error ends the run cleanly with
whatever was collected so far.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ..benchmark.cases import DefenseVariant, LionCase
from ..personas import INVESTOR_IDS
from ..pitch import validate_team_name
from .client import Api, ApiError

# One invocation plays one session with at most this many pitches (teams).
MAX_PITCHES = 10
INITIAL_CALLS_PER_PITCH = 2 * len(INVESTOR_IDS)
# One retry per team and step: at most four more round 1 calls and four more verdict calls.
MAX_CALLS_PER_PITCH = 4 * len(INVESTOR_IDS)

# How long a retried step may keep showing its old failure before it counts as failed again.
RETRY_GRACE_SECONDS = 30.0

Log = Callable[[str], None]


class RunAborted(RuntimeError):
    """The run cannot continue; what was collected so far is still reported."""

    def __init__(self, message: str, status: str = "aborted") -> None:
        super().__init__(message)
        self.status = status


class LimitError(ValueError):
    """The requested configuration is not allowed."""


@dataclass
class Settings:
    poll_interval: float = 2.0
    jury_timeout: float = 600.0
    request_timeout: float = 30.0


@dataclass
class Calls:
    round1: int = 0
    verdict: int = 0
    round1_retry: int = 0
    verdict_retry: int = 0

    @property
    def total(self) -> int:
        return self.round1 + self.verdict + self.round1_retry + self.verdict_retry

    def as_dict(self) -> dict[str, int]:
        return {
            "round1": self.round1,
            "verdict": self.verdict,
            "round1Retry": self.round1_retry,
            "verdictRetry": self.verdict_retry,
            "total": self.total,
        }


@dataclass
class Scenario:
    """One planned pitch: a case, one defense variant and the team that plays it."""

    index: int
    case: LionCase
    variant: DefenseVariant
    team_name: str
    api: Api | None = None
    team_id: str | None = None
    pitch_updated_at: int | None = None
    status: str = "planned"
    error: str | None = None
    calls: Calls = field(default_factory=Calls)
    retried: dict[str, bool] = field(default_factory=dict)
    retry_fingerprint: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    retry_at: dict[str, float] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.status == "active"

    def fail(self, status: str, error: str) -> None:
        self.status = status
        self.error = error


def check_limit(cases: list[LionCase]) -> list[tuple[LionCase, DefenseVariant]]:
    """The pitches to play, or a LimitError before anything is sent."""
    pitches = [(case, variant) for case in cases for variant in case.variants]
    if not pitches:
        raise LimitError("The selected test set is empty.")
    if len(pitches) > MAX_PITCHES:
        raise LimitError(
            f"{len(pitches)} pitches were selected, but one run plays at most {MAX_PITCHES}. "
            "Use --only to select fewer cases."
        )
    return pitches


def plan_scenarios(cases: list[LionCase]) -> list[Scenario]:
    """One scenario per defense scenario, with team names that stay neutral.

    The jury sees the team name, so it is the startup name and never says which defense
    variant a team plays. Names must be unique, so a repeated startup name (the paired
    case plays one pitch twice) gets a plain number.
    """
    scenarios: list[Scenario] = []
    used: set[str] = set()
    for case, variant in check_limit(cases):
        base = case.pitch.startup_name
        name = base
        number = 1
        while name.lower() in used:
            number += 1
            name = f"{base} {number}"
        validation = validate_team_name(name)
        if not validation.ok or validation.name is None:
            raise LimitError(f"Invalid team name {name!r}: {validation.error}")
        used.add(validation.name.lower())
        scenarios.append(Scenario(len(scenarios), case, variant, validation.name))
    return scenarios


def expected_calls(pitches: int) -> int:
    return pitches * INITIAL_CALLS_PER_PITCH


def worst_case_calls(pitches: int) -> int:
    return pitches * MAX_CALLS_PER_PITCH


def pitch_payload(case: LionCase) -> dict[str, str]:
    pitch = case.pitch
    return {
        "startupName": pitch.startup_name,
        "tagline": pitch.tagline,
        "problem": pitch.problem,
        "solution": pitch.solution,
        "customers": pitch.customers,
        "businessModel": pitch.business_model,
    }


@dataclass
class LiveRun:
    """Everything one invocation collected, complete or not."""

    scenarios: list[Scenario]
    outcome: str = "aborted"
    abort_reason: str | None = None
    stage: str = "start"
    final_view: dict[str, Any] | None = None
    mode: dict[str, Any] = field(default_factory=dict)
    previous_session: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def calls(self) -> Calls:
        total = Calls()
        for scenario in self.scenarios:
            total.round1 += scenario.calls.round1
            total.verdict += scenario.calls.verdict
            total.round1_retry += scenario.calls.round1_retry
            total.verdict_retry += scenario.calls.verdict_retry
        return total


def _first_error(entries: dict[str, dict[str, Any]]) -> str:
    for investor_id in INVESTOR_IDS:
        entry = entries.get(investor_id) or {}
        if entry.get("status") == "failed":
            return f"{investor_id}: {entry.get('error') or 'failed'}"
    return "unknown failure"


def _fingerprint(entries: dict[str, dict[str, Any]]) -> tuple[Any, ...]:
    return tuple(
        (
            (entries.get(investor_id) or {}).get("status"),
            (entries.get(investor_id) or {}).get("finishedAt"),
        )
        for investor_id in INVESTOR_IDS
    )


def _step_state(entries: dict[str, dict[str, Any]]) -> str:
    """done, working (a call is pending or running) or failed (settled with a failure)."""
    statuses = [(entries.get(investor_id) or {}).get("status") for investor_id in INVESTOR_IDS]
    if all(status == "done" for status in statuses):
        return "done"
    if any(status in ("pending", "running") or status is None for status in statuses):
        return "working"
    return "failed"


class LiveSession:
    def __init__(
        self,
        *,
        base_url: str,
        password: str,
        scenarios: list[Scenario],
        settings: Settings,
        run_dir: Path,
        transport: httpx.AsyncBaseTransport | None = None,
        log: Log = print,
    ) -> None:
        self._base_url = base_url
        self._password = password
        self._settings = settings
        self._run_dir = run_dir
        self._transport = transport
        self._log_sink = log
        self._started = time.monotonic()
        self._moderator: Api | None = None
        # Until the reset has happened the live session is still somebody else's.
        self._reset_done = False
        self.run = LiveRun(scenarios=scenarios)

    # Logging

    def _log(self, message: str) -> None:
        elapsed = round(time.monotonic() - self._started, 1)
        self.run.events.append({"seconds": elapsed, "message": message})
        self._log_sink(f"[{elapsed:7.1f}s] {message}")

    @property
    def _scenarios(self) -> list[Scenario]:
        return self.run.scenarios

    def _active(self) -> list[Scenario]:
        return [scenario for scenario in self._scenarios if scenario.active]

    def _api(self) -> Api:
        return Api(
            self._base_url, timeout=self._settings.request_timeout, transport=self._transport
        )

    # Entry point

    async def play(self) -> LiveRun:
        run = self.run
        self._moderator = self._api()
        try:
            await self._prepare()
            await self._register()
            await self._ideation()
            await self._jury_round1()
            await self._defense()
            await self._verdict()
            await self._leaderboard()
            run.stage = "finished"
            for scenario in self._active():
                scenario.status = "done"
        except RunAborted as error:
            run.abort_reason = str(error)
            self._log(f"Run ended early at stage '{run.stage}': {error}")
            for scenario in self._active():
                scenario.fail(error.status, str(error))
        except ApiError as error:
            run.abort_reason = str(error)
            self._log(f"Run ended early at stage '{run.stage}': {error}")
            for scenario in self._active():
                scenario.fail("error", str(error))
        finally:
            await self._collect_final_view()
            await self._close()
        run.timings["totalSeconds"] = round(time.monotonic() - self._started, 1)
        failed = [scenario for scenario in self._scenarios if scenario.status != "done"]
        if run.abort_reason is not None:
            run.outcome = "aborted"
        else:
            run.outcome = "partial" if failed else "completed"
        return run

    async def _close(self) -> None:
        for scenario in self._scenarios:
            if scenario.api is not None:
                await scenario.api.aclose()
        if self._moderator is not None:
            await self._moderator.aclose()

    async def _collect_final_view(self) -> None:
        assert self._moderator is not None
        if not self._reset_done:
            return
        try:
            self.run.final_view = await self._moderator.moderator_state()
        except ApiError as error:
            self._log(f"Could not read the final moderator view: {error}")

    # Stages

    async def _prepare(self) -> None:
        """Log in, keep the old session, reset it and start from an empty lobby."""
        run = self.run
        run.stage = "prepare"
        moderator = self._moderator
        assert moderator is not None
        await moderator.login(self._password)
        self._log("Moderator login ok.")
        view = await moderator.moderator_state()
        mode = view.get("mode") or {}
        run.mode = {
            "demo": bool(mode.get("demo")),
            "provider": mode.get("provider"),
            "model": mode.get("model"),
        }
        self._log(f"Target jury: {run.mode['provider']} ({run.mode['model']}).")
        teams = view.get("teams") or []
        session = view.get("session") or {}
        previous: dict[str, Any] = {
            "phase": session.get("phase"),
            "teams": len(teams),
            "exportFile": None,
            "archivedId": None,
        }
        if teams:
            exported = await moderator.export_json()
            export_path = self._run_dir / "previous-session-export.json"
            export_path.write_text(exported, encoding="utf-8")
            previous["exportFile"] = export_path.name
            self._log(
                f"Exported the existing session ({len(teams)} teams, phase "
                f"'{previous['phase']}') to {export_path}."
            )
        run.previous_session = previous
        result = await moderator.reset()
        self._reset_done = True
        archived_id = result.get("archivedId")
        previous["archivedId"] = archived_id
        if teams:
            if not isinstance(archived_id, str) or not archived_id:
                raise RunAborted("The reset did not report an archive for the old session.")
            await moderator.archive(archived_id)
            self._log(f"Reset done. The old session is archived as {archived_id}.")
        else:
            self._log("Reset done. The old session was empty, nothing to archive.")

    async def _register(self) -> None:
        self.run.stage = "register"
        for scenario in self._scenarios:
            api = self._api()
            scenario.api = api
            try:
                joined = await api.join(scenario.team_name)
                team = joined.get("team") or {}
                scenario.team_id = team.get("id")
                scenario.status = "active"
                scenario.pitch_updated_at = team.get("pitchUpdatedAt")
            except ApiError as error:
                scenario.fail("error", str(error))
                self._log(f"Team '{scenario.team_name}' could not join: {error}")
        self._require_active("No team could join.")
        self._log(f"{len(self._active())} teams joined.")

    async def _ideation(self) -> None:
        run = self.run
        run.stage = "ideation"
        await self._advance("lobby", "ideation")
        for scenario in self._active():
            assert scenario.api is not None
            try:
                response = await scenario.api.submit_pitch(
                    pitch_payload(scenario.case),
                    scenario.pitch_updated_at,
                )
                if (response.get("team") or {}).get("submittedAt") is None:
                    raise ApiError("submit pitch", "the pitch was not recorded as submitted")
            except ApiError as error:
                scenario.fail("error", str(error))
                self._log(f"Team '{scenario.team_name}' could not submit its pitch: {error}")
        self._require_active("No pitch could be submitted.")
        self._log(f"{len(self._active())} pitches submitted.")

    async def _jury_round1(self) -> None:
        run = self.run
        run.stage = "jury-round1"
        started = time.monotonic()
        await self._advance("ideation", "jury")
        for scenario in self._active():
            scenario.calls.round1 += len(INVESTOR_IDS)
        self._log(f"Jury round 1 started: {run.calls.round1} calls.")
        view = await self._wait_for_step("round1")
        run.timings["round1Seconds"] = round(time.monotonic() - started, 1)
        self._require_active("Every team failed in jury round 1.")
        phase = (view.get("session") or {}).get("phase")
        if phase == "jury":
            # "Start now": the moderator moves on instead of waiting for the countdown.
            await self._advance("jury", "defense", tolerate_concurrent=True)
        elif phase == "defense":
            self._log("The jury round already advanced to the defense by itself.")
        else:
            raise RunAborted(f"Unexpected phase '{phase}' after jury round 1.")

    async def _defense(self) -> None:
        self.run.stage = "defense"
        for scenario in self._active():
            assert scenario.api is not None
            try:
                response = await scenario.api.submit_defense(
                    [scenario.variant.answers[investor_id] for investor_id in INVESTOR_IDS],
                    scenario.variant.one_change,
                    None,
                )
                defense = (response.get("team") or {}).get("defense") or {}
                if defense.get("submittedAt") is None:
                    raise ApiError("submit defense", "the defense was not recorded as submitted")
            except ApiError as error:
                scenario.fail("error", str(error))
                self._log(f"Team '{scenario.team_name}' could not submit its defense: {error}")
        self._require_active("No defense could be submitted.")
        self._log(f"{len(self._active())} defenses submitted.")

    async def _verdict(self) -> None:
        run = self.run
        run.stage = "verdict"
        started = time.monotonic()
        await self._advance("defense", "verdict")
        for scenario in self._active():
            scenario.calls.verdict += len(INVESTOR_IDS)
        self._log(f"Final verdict started: {len(self._active()) * len(INVESTOR_IDS)} calls.")
        await self._wait_for_step("verdict")
        run.timings["verdictSeconds"] = round(time.monotonic() - started, 1)

    async def _leaderboard(self) -> None:
        self.run.stage = "leaderboard"
        await self._advance("verdict", "leaderboard")
        self._log("Leaderboard reached; the ranking is final.")

    # Helpers

    def _require_active(self, message: str) -> None:
        if not self._active():
            raise RunAborted(message, status="error")

    async def _advance(
        self, current: str, target: str, *, tolerate_concurrent: bool = False
    ) -> None:
        """Advance once with expectedPhase, and reconcile if the answer is unclear."""
        assert self._moderator is not None
        try:
            payload = await self._moderator.advance(current)
            phase = (payload.get("session") or {}).get("phase")
        except ApiError as error:
            if error.status not in (None, 409):
                raise
            view = await self._moderator.moderator_state()
            phase = (view.get("session") or {}).get("phase")
            if phase != target or (error.status == 409 and not tolerate_concurrent):
                raise RunAborted(
                    f"Could not advance from '{current}' to '{target}' "
                    f"(the server is in '{phase}'): {error}"
                ) from error
            self._log(f"The session was already in '{target}'.")
            return
        if phase != target:
            raise RunAborted(
                f"Expected phase '{target}' after advancing from '{current}', got '{phase}'."
            )
        self._log(f"Advanced {current} -> {target}.")

    async def _wait_for_step(self, step: str) -> dict[str, Any]:
        """Poll the moderator view until every active team's step is done or given up.

        A failed step is retried through the moderator retry once per team and step.
        """
        moderator = self._moderator
        assert moderator is not None
        deadline = time.monotonic() + self._settings.jury_timeout
        while True:
            view = await moderator.moderator_state()
            teams = {team["id"]: team for team in view.get("teams") or []}
            working = False
            for scenario in self._active():
                team = teams.get(scenario.team_id or "")
                if team is None:
                    scenario.fail("error", "The team disappeared from the moderator view.")
                    continue
                if step == "round1" and team.get("eligible") is not True:
                    scenario.fail("error", "The pitch was not accepted for the jury.")
                    continue
                if await self._settle(scenario, step, team.get(step) or {}):
                    working = True
            if not working:
                return view
            if time.monotonic() >= deadline:
                label = "jury round 1" if step == "round1" else "the final verdict"
                raise RunAborted(
                    f"Timed out after {self._settings.jury_timeout:.0f}s waiting for {label}.",
                    status="timeout",
                )
            await asyncio.sleep(self._settings.poll_interval)

    async def _settle(
        self, scenario: Scenario, step: str, entries: dict[str, dict[str, Any]]
    ) -> bool:
        """Handle one team's step. True while it is still working."""
        moderator = self._moderator
        assert moderator is not None
        state = _step_state(entries)
        if state == "done":
            return False
        if state == "working":
            return True
        # Settled with a failure.
        if scenario.retried.get(step):
            still_old = _fingerprint(entries) == scenario.retry_fingerprint.get(step)
            waited = time.monotonic() - scenario.retry_at.get(step, 0.0)
            if still_old and waited < RETRY_GRACE_SECONDS:
                return True  # the retry has not started yet
            reason = _first_error(entries)
            scenario.fail(f"{step}-failed", reason)
            self._log(f"Team '{scenario.team_name}' failed in {step} after one retry: {reason}")
            return False
        failed_calls = sum(
            1
            for investor_id in INVESTOR_IDS
            if (entries.get(investor_id) or {}).get("status") == "failed"
        )
        try:
            await moderator.retry(scenario.team_id or "", step)
        except ApiError as error:
            if error.status == 409:
                return True  # a run is still active; look again
            scenario.fail(f"{step}-failed", str(error))
            return False
        scenario.retried[step] = True
        scenario.retry_fingerprint[step] = _fingerprint(entries)
        scenario.retry_at[step] = time.monotonic()
        if step == "round1":
            scenario.calls.round1_retry += failed_calls
        else:
            # A verdict retry runs all four verdict calls again.
            scenario.calls.verdict_retry += len(INVESTOR_IDS)
        self._log(
            f"Retry {step} for team '{scenario.team_name}' "
            f"({failed_calls if step == 'round1' else len(INVESTOR_IDS)} calls)."
        )
        return True


def started_at_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
