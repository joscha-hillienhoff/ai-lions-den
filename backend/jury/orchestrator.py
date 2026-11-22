"""Background jury orchestration.

Every step is persisted before and after the model call, so page requests never
wait for a model call and a restart can pick up unfinished work.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from functools import partial
from typing import Any

from ..auto_advance import apply_auto_advance, auto_advance_action
from ..config import AppConfig
from ..personas import INVESTOR_IDS, InvestorId, investor_by_id
from ..ranking import (
    ranked_teams_for_state,
    round1_has_failure,
    round1_is_complete,
    verdict_has_failure,
    verdict_is_complete,
)
from ..state import PhaseId, SessionState, Team, is_at_least
from ..store import Store
from .types import JuryProvider, Round1Summary, VerdictInput
from .validation import clamp_verdict_score, decision_for_score

logger = logging.getLogger(__name__)


class TaskLimiter:
    def __init__(self, maximum: int) -> None:
        self._semaphore = asyncio.Semaphore(maximum)

    async def run(self, task: Any) -> Any:
        async with self._semaphore:
            return await task()


def _error_message(error: Exception) -> str:
    return re.sub(r"\s+", " ", str(error)).strip()[:400]


def _round1_call_key(team_id: str, investor_id: InvestorId) -> str:
    return f"round1:{team_id}:{investor_id}"


def _verdict_key(team_id: str) -> str:
    return f"verdict:{team_id}"


class JuryOrchestrator:
    def __init__(self, store: Store, provider: JuryProvider, config: AppConfig) -> None:
        self._store = store
        self._provider = provider
        self._limiter = TaskLimiter(config.max_concurrency)
        self._active: set[str] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._last_resume_at = 0.0
        self._auto_advance_ms = config.jury_auto_advance_ms
        self._auto_advance_timer: asyncio.Task[Any] | None = None
        self._auto_advance_timer_at: int | None = None

    @property
    def active_job_count(self) -> int:
        return len(self._active)

    def tick_auto_advance(self, now: int | None = None) -> bool:
        """Arm, clear or fire the jury round's auto-advance countdown, then keep its timer.

        Safe to call from anywhere in the running loop and as often as needed: it only
        writes when the state has to change. The transition runs inside one store
        transaction with the same code path as a manual advance, so it happens exactly
        once however many callers, timers or tabs race. Returns True when this call
        moved the session to the defense.
        """
        moment = _now_ms() if now is None else now
        delay = self._auto_advance_ms
        advanced = False
        if auto_advance_action(self._store.read(), moment, delay) is not None:
            advanced = self._store.update(lambda state: apply_auto_advance(state, moment, delay))
        state = self._store.read()
        if advanced:
            self.handle_phase_entered(state.phase)
        self._schedule_auto_advance(state.auto_advance_at if state.phase == "jury" else None)
        return advanced

    def _schedule_auto_advance(self, deadline: int | None) -> None:
        """Keep exactly one timer that ticks at the countdown deadline, none without one."""
        timer = self._auto_advance_timer
        if deadline is None:
            if timer is not None:
                timer.cancel()
            self._auto_advance_timer = None
            self._auto_advance_timer_at = None
            return
        if timer is not None and not timer.done() and self._auto_advance_timer_at == deadline:
            return
        if timer is not None:
            timer.cancel()
        self._auto_advance_timer_at = deadline
        self._auto_advance_timer = asyncio.get_running_loop().create_task(
            self._auto_advance_at(deadline)
        )

    async def _auto_advance_at(self, deadline: int) -> None:
        while (remaining := deadline - _now_ms()) > 0:
            await asyncio.sleep(remaining / 1000)
        if self._auto_advance_timer_at == deadline:
            self._auto_advance_timer = None
            self._auto_advance_timer_at = None
        self.tick_auto_advance()

    async def run_round1(self, team_id: str, *, retry_failed: bool = False) -> None:
        team = self._find_team(team_id)
        if team is None or team.eligible is not True:
            return

        calls = []
        for investor_id in INVESTOR_IDS:
            status = team.round1[investor_id].status
            if status != "pending" and not (retry_failed and status == "failed"):
                continue
            calls.append(self._run_round1_call(team_id, investor_id))
        await asyncio.gather(*calls)

    async def run_verdict(self, team_id: str, *, reset: bool = False) -> None:
        if _verdict_key(team_id) in self._active:
            return

        initial = self._find_team(team_id)
        if initial is None or initial.eligible is not True or not round1_is_complete(initial):
            return
        if verdict_is_complete(initial) and not reset:
            return
        if verdict_has_failure(initial) and not reset:
            return

        if reset:
            self._store.update(lambda state: _reset_verdict(state, team_id))

        self._active.add(_verdict_key(team_id))
        try:
            state = self._store.read()
            team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
            if team is None:
                return

            investor_ids = [
                investor_id
                for investor_id in INVESTOR_IDS
                if team.verdict[investor_id].status in ("pending", "running")
            ]
            await asyncio.gather(
                *(self._run_verdict_call(team_id, investor_id) for investor_id in investor_ids)
            )
        finally:
            self._active.discard(_verdict_key(team_id))

    async def retry_round1(self, team_id: str) -> None:
        await self.run_round1(team_id, retry_failed=True)

        state = self._store.read()
        team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
        if team is None or not is_at_least(state.phase, "verdict"):
            return
        if not round1_is_complete(team):
            return

        # A changed round 1 must not leave an inconsistent final verdict behind.
        self._store.update(lambda next_state: _reset_verdict(next_state, team_id))
        await self.run_verdict(team_id)

    def verdict_is_active(self, team_id: str) -> bool:
        return _verdict_key(team_id) in self._active

    async def retry_verdict(self, team_id: str) -> None:
        await self.run_verdict(team_id, reset=True)

    def handle_phase_entered(self, phase: PhaseId) -> None:
        # A forward entry into the jury phase with every result already in arms the
        # countdown at once; any other phase change ends a running one.
        self.tick_auto_advance()
        if not is_at_least(phase, "jury"):
            return
        state = self._store.read()

        for team in state.teams:
            if team.eligible is not True:
                continue
            if any(team.round1[investor_id].status == "pending" for investor_id in INVESTOR_IDS):
                self._kick(self.run_round1(team.id))
            if (
                is_at_least(phase, "verdict")
                and round1_is_complete(team)
                and not verdict_is_complete(team)
                and not round1_has_failure(team)
            ):
                self._kick(self.run_verdict(team.id))

    def resume(self) -> None:
        """Poll-driven recovery: repairs steps left running and starts pending work."""
        now = time.time() * 1000
        if now - self._last_resume_at < 1_200:
            return
        self._last_resume_at = now

        # A safety net for the timer, for example after a restart.
        self.tick_auto_advance()

        state = self._store.read()
        stale_running = any(
            (
                team.round1[investor_id].status == "running"
                and _round1_call_key(team.id, investor_id) not in self._active
            )
            or (
                team.verdict[investor_id].status == "running"
                and _verdict_key(team.id) not in self._active
            )
            for team in state.teams
            for investor_id in INVESTOR_IDS
        )

        if stale_running:
            current = self._store.update(
                lambda next_state: _repair_running(next_state, self._active)
            )
        else:
            current = state

        if not is_at_least(current.phase, "jury"):
            return

        for team in current.teams:
            if team.eligible is not True:
                continue
            if any(team.round1[investor_id].status == "pending" for investor_id in INVESTOR_IDS):
                self._kick(self.run_round1(team.id))
            if (
                is_at_least(current.phase, "verdict")
                and round1_is_complete(team)
                and not verdict_is_complete(team)
                and not round1_has_failure(team)
                and not verdict_has_failure(team)
            ):
                self._kick(self.run_verdict(team.id))

    def rank(self, state: SessionState | None = None) -> list[Any]:
        return ranked_teams_for_state(state or self._store.read())

    def _find_team(self, team_id: str) -> Team | None:
        return next((team for team in self._store.read().teams if team.id == team_id), None)

    async def _run_round1_call(self, team_id: str, investor_id: InvestorId) -> None:
        key = _round1_call_key(team_id, investor_id)
        if key in self._active:
            return
        self._active.add(key)

        try:
            team = self._find_team(team_id)
            if team is None or team.eligible is not True:
                return
            if team.round1[investor_id].status == "done":
                return

            self._store.update(lambda state: _mark_round1_running(state, team_id, investor_id))

            try:
                output = await self._limiter.run(
                    lambda: self._provider.evaluate_round1(
                        _round1_input(team_id, team, investor_id)
                    )
                )

                self._store.update(
                    lambda state: _mark_round1_done(state, team_id, investor_id, output)
                )
                self._maybe_continue_to_verdict(team_id)
                self.tick_auto_advance()
            except Exception as error:  # noqa: BLE001 - report provider errors to the moderator
                message = _error_message(error)
                logger.error(
                    "[jury] round 1 failed team=%s investor=%s: %s",
                    team_id,
                    investor_id,
                    message,
                )
                self._store.update(
                    lambda state: _mark_round1_failed(state, team_id, investor_id, message)
                )
                self.tick_auto_advance()
        finally:
            self._active.discard(key)

    async def _run_verdict_call(self, team_id: str, investor_id: InvestorId) -> None:
        state = self._store.read()
        team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
        if team is None or team.eligible is not True:
            return
        if team.verdict[investor_id].status not in ("pending", "running"):
            return

        input_value = self._build_verdict_input(state, team, investor_id)
        if input_value is None:
            return

        self._store.update(
            partial(
                _mark_verdict_running,
                team_id=team_id,
                investor_id=investor_id,
            )
        )

        try:
            output = await self._limiter.run(partial(self._provider.evaluate_verdict, input_value))
            self._store.update(
                partial(
                    _mark_verdict_done,
                    team_id=team_id,
                    investor_id=investor_id,
                    output=output,
                )
            )
        except Exception as error:  # noqa: BLE001 - report provider errors to the moderator
            message = _error_message(error)
            logger.error(
                "[jury] verdict failed team=%s investor=%s: %s",
                team_id,
                investor_id,
                message,
            )
            self._store.update(
                partial(
                    _mark_verdict_failed,
                    team_id=team_id,
                    investor_id=investor_id,
                    message=message,
                )
            )

    def _maybe_continue_to_verdict(self, team_id: str) -> None:
        state = self._store.read()
        if not is_at_least(state.phase, "verdict"):
            return
        team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
        if team is None or not round1_is_complete(team) or verdict_is_complete(team):
            return
        self._kick(self.run_verdict(team_id))

    def start_background(self, coroutine: Any) -> None:
        self._kick(coroutine)

    def _kick(self, coroutine: Any) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self._tasks.add(task)

        def _done(finished: asyncio.Task[Any]) -> None:
            self._tasks.discard(finished)
            if finished.cancelled():
                return
            error = finished.exception()
            if error is not None:
                logger.error("[jury] background job failed: %s", error)

        task.add_done_callback(_done)

    def _build_verdict_input(
        self, state: SessionState, team: Team, investor_id: InvestorId
    ) -> VerdictInput | None:
        round1: list[Round1Summary] = []
        for entry_id in INVESTOR_IDS:
            entry = team.round1[entry_id]
            if entry.status != "done" or entry.score is None:
                return None
            round1.append(
                Round1Summary(
                    investor_id=entry_id,
                    investor_name=investor_by_id(entry_id).name,
                    score=entry.score,
                    assessment=entry.assessment or "",
                    question=entry.question or "",
                )
            )

        answer_index = INVESTOR_IDS.index(investor_id)
        answer = (
            team.defense.answers[answer_index] if answer_index < len(team.defense.answers) else ""
        )
        return VerdictInput(
            team_id=team.id,
            team_name=team.name,
            pitch=team.pitch,
            investor=investor_by_id(investor_id),
            answer=answer,
            one_change=team.defense.one_change,
            round1=round1,
            previous=[],
        )


def _round1_input(team_id: str, team: Team, investor_id: InvestorId) -> Any:
    from .types import Round1Input

    return Round1Input(
        team_id=team_id,
        team_name=team.name,
        pitch=team.pitch,
        investor=investor_by_id(investor_id),
    )


def _reset_verdict(state: SessionState, team_id: str) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import empty_verdict

    team.verdict = empty_verdict()
    team.verdict_completed_at = None


def _mark_verdict_running(state: SessionState, team_id: str, investor_id: InvestorId) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import VerdictEntry

    team.verdict[investor_id] = VerdictEntry(status="running", started_at=_now_ms())


def _mark_verdict_done(
    state: SessionState,
    team_id: str,
    investor_id: InvestorId,
    output: Any,
) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import VerdictEntry

    round1_score = team.round1[investor_id].score
    score = clamp_verdict_score(output.score, round1_score)
    team.verdict[investor_id] = VerdictEntry(
        status="done",
        statement=output.statement,
        score=score,
        decision=decision_for_score(score),
        finished_at=_now_ms(),
    )
    if all(team.verdict[entry_id].status == "done" for entry_id in INVESTOR_IDS):
        team.verdict_completed_at = _now_ms()


def _mark_verdict_failed(
    state: SessionState, team_id: str, investor_id: InvestorId, message: str
) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import VerdictEntry

    team.verdict[investor_id] = VerdictEntry(status="failed", error=message, finished_at=_now_ms())


def _mark_round1_running(state: SessionState, team_id: str, investor_id: InvestorId) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import Round1Entry

    team.round1[investor_id] = Round1Entry(status="running", started_at=_now_ms())


def _mark_round1_done(
    state: SessionState, team_id: str, investor_id: InvestorId, output: Any
) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import Round1Entry

    team.round1[investor_id] = Round1Entry(
        status="done",
        score=output.score,
        assessment=output.assessment,
        question=output.question,
        finished_at=_now_ms(),
    )


def _mark_round1_failed(
    state: SessionState, team_id: str, investor_id: InvestorId, message: str
) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    from ..state import Round1Entry

    team.round1[investor_id] = Round1Entry(status="failed", error=message, finished_at=_now_ms())


def _repair_running(state: SessionState, active: set[str]) -> SessionState:
    from ..state import Round1Entry, VerdictEntry

    for team in state.teams:
        for investor_id in INVESTOR_IDS:
            if (
                team.round1[investor_id].status == "running"
                and _round1_call_key(team.id, investor_id) not in active
            ):
                team.round1[investor_id] = Round1Entry(status="pending")
            if (
                team.verdict[investor_id].status == "running"
                and _verdict_key(team.id) not in active
            ):
                team.verdict[investor_id] = VerdictEntry(status="pending")
    return state


def _now_ms() -> int:
    return int(time.time() * 1000)
