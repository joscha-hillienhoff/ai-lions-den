"""Drive the real round 1 -> defense -> verdict path for one benchmark case."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..config import AppConfig
from ..jury.orchestrator import JuryOrchestrator
from ..jury.types import JuryProvider
from ..personas import INVESTOR_IDS
from ..state import Defense, SessionState, Team, empty_round1, empty_verdict
from ..store import Store
from .cases import DefenseVariant, LionCase
from .meter import BudgetExceeded, UsageMeter

ProgressCallback = Callable[[int, int, dict[str, Any]], None]


class MeteredJuryProvider:
    """Checks budget headroom before every model call, then delegates."""

    def __init__(self, provider: JuryProvider, meter: UsageMeter) -> None:
        self._provider = provider
        self._meter = meter
        self.name = provider.name

    async def evaluate_round1(self, input_value: Any) -> Any:
        reservation = self._meter.before_call()
        with self._meter.reservation_scope(reservation):
            try:
                return await self._provider.evaluate_round1(input_value)
            except Exception:
                self._meter.charge_reservation()
                raise

    async def evaluate_verdict(self, input_value: Any) -> Any:
        reservation = self._meter.before_call()
        with self._meter.reservation_scope(reservation):
            try:
                return await self._provider.evaluate_verdict(input_value)
            except Exception:
                self._meter.charge_reservation()
                raise


def _seed_state(state: SessionState, team: Team, now: int) -> None:
    state.phase = "jury"
    state.phase_started_at = now
    state.teams = [team]


def _prepare_verdict(state: SessionState, team_id: str, variant: DefenseVariant, now: int) -> None:
    team = next((candidate for candidate in state.teams if candidate.id == team_id), None)
    if team is None:
        return
    team.defense = Defense(
        answers=[variant.answers[investor_id] for investor_id in INVESTOR_IDS],
        one_change=variant.one_change,
        submitted_at=now,
    )
    state.phase = "verdict"
    state.phase_started_at = now


def _entry_dict(entry: Any) -> dict[str, Any]:
    # Round 1 entries have an assessment and a question; verdict entries have a
    # statement and a decision. Only the shared fields are guaranteed.
    return {
        "status": entry.status,
        "score": entry.score,
        "text": getattr(entry, "assessment", None) or getattr(entry, "statement", None),
        "question": getattr(entry, "question", None),
        "decision": getattr(entry, "decision", None),
        "error": entry.error,
    }


def _round1_payload(team: Team) -> dict[str, Any]:
    entries = {investor_id: _entry_dict(team.round1[investor_id]) for investor_id in INVESTOR_IDS}
    return {
        "total": sum(entry["score"] or 0 for entry in entries.values()),
        "investors": entries,
    }


def _verdict_payload(team: Team) -> dict[str, Any]:
    entries = {investor_id: _entry_dict(team.verdict[investor_id]) for investor_id in INVESTOR_IDS}
    return {
        "total": sum(entry["score"] or 0 for entry in entries.values()),
        "inCount": sum(1 for entry in entries.values() if entry["decision"] == "in"),
        "investors": entries,
    }


def _incomplete(entries: dict[str, dict[str, Any]]) -> bool:
    return any(entry["status"] != "done" for entry in entries.values())


async def run_session(
    *,
    case: LionCase,
    variant: DefenseVariant,
    repeat: int,
    provider: JuryProvider,
    config: AppConfig,
    meter: UsageMeter,
    now: int | None = None,
) -> dict[str, Any]:
    """Run one simulated session and return raw, JSON-safe results.

    The orchestration, validation, score clamping and decision derivation all
    come from the app code itself; only the provider is injected.
    """
    started_at = int(time.time() * 1000) if now is None else now
    team_id = f"{case.id}-{variant.id}-r{repeat}"
    store = Store(":memory:")
    try:
        team = Team(
            id=team_id,
            name=case.pitch.startup_name,
            token=f"benchmark-{team_id}",
            created_at=started_at,
            pitch=case.pitch.to_pitch(),
            pitch_updated_at=started_at,
            submitted_at=started_at,
            eligible=True,
            round1=empty_round1(),
            defense=Defense(),
            verdict=empty_verdict(),
        )
        store.update(lambda state: _seed_state(state, team, started_at))
        orchestrator = JuryOrchestrator(store, provider, config)
        started = time.perf_counter()

        await orchestrator.run_round1(team_id)
        current = store.read()
        current_team = next((item for item in current.teams if item.id == team_id), None)
        if current_team is None:
            return _base_result(case, variant, repeat, team_id, "error", started) | {
                "error": "The team disappeared from the store.",
            }

        round1 = _round1_payload(current_team)
        result = _base_result(case, variant, repeat, team_id, "round1", started) | {
            "round1": round1
        }

        if meter.aborted:
            result["status"] = "budget-aborted"
            result["error"] = meter.abort_reason
            result["durationMs"] = _millis(started)
            return result
        if _incomplete(round1["investors"]):
            result["status"] = "round1-failed"
            result["error"] = _first_error(round1["investors"])
            result["durationMs"] = _millis(started)
            return result

        store.update(lambda state: _prepare_verdict(state, team_id, variant, started_at + 1))
        await orchestrator.run_verdict(team_id)
        current = store.read()
        current_team = next((item for item in current.teams if item.id == team_id), None)
        if current_team is None:
            result["status"] = "error"
            result["error"] = "The team disappeared from the store after the defense."
            result["durationMs"] = _millis(started)
            return result

        verdict = _verdict_payload(current_team)
        result["verdict"] = verdict
        result["durationMs"] = _millis(started)
        if meter.aborted:
            result["status"] = "budget-aborted"
            result["error"] = meter.abort_reason
        elif _incomplete(verdict["investors"]):
            result["status"] = "verdict-failed"
            result["error"] = _first_error(verdict["investors"])
        else:
            result["status"] = "done"
        return result
    finally:
        store.close()


def _base_result(
    case: LionCase,
    variant: DefenseVariant,
    repeat: int,
    team_id: str,
    status: str,
    started: float,
) -> dict[str, Any]:
    return {
        "caseId": case.id,
        "caseTier": case.tier,
        "language": case.language,
        "tags": list(case.tags),
        "variantId": variant.id,
        "variantKind": variant.kind,
        "repeat": repeat,
        "teamId": team_id,
        "status": status,
        "durationMs": _millis(started),
        "round1": None,
        "defense": {
            "answers": {investor_id: variant.answers[investor_id] for investor_id in INVESTOR_IDS},
            "oneChange": variant.one_change,
        },
        "verdict": None,
        "error": None,
    }


def _millis(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _first_error(entries: dict[str, dict[str, Any]]) -> str:
    for investor_id in INVESTOR_IDS:
        entry = entries[investor_id]
        if entry["status"] != "done":
            detail = entry.get("error") or entry["status"]
            return f"{investor_id}: {detail}"
    return "unknown failure"


async def run_benchmark(
    *,
    cases: list[LionCase],
    repeats: int,
    provider: JuryProvider,
    meter: UsageMeter,
    config: AppConfig,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    planned = sum(len(case.variants) for case in cases) * repeats
    metered = MeteredJuryProvider(provider, meter)
    runs: list[dict[str, Any]] = []
    aborted = False
    abort_reason: str | None = None
    index = 0

    for case in cases:
        for variant in case.variants:
            for repeat in range(1, repeats + 1):
                before = meter.snapshot()
                try:
                    run = await run_session(
                        case=case,
                        variant=variant,
                        repeat=repeat,
                        provider=metered,
                        config=config,
                        meter=meter,
                    )
                except BudgetExceeded as error:
                    aborted = True
                    abort_reason = str(error)
                    run = {
                        "caseId": case.id,
                        "caseTier": case.tier,
                        "variantId": variant.id,
                        "variantKind": variant.kind,
                        "repeat": repeat,
                        "status": "budget-aborted",
                        "error": str(error),
                        "round1": None,
                        "verdict": None,
                    }
                except Exception as error:  # noqa: BLE001 - one broken run must not stop the suite
                    run = {
                        "caseId": case.id,
                        "caseTier": case.tier,
                        "variantId": variant.id,
                        "variantKind": variant.kind,
                        "repeat": repeat,
                        "status": "error",
                        "error": f"{type(error).__name__}: {error}",
                        "round1": None,
                        "verdict": None,
                    }

                after = meter.snapshot()
                run["spendUsd"] = round(after.usd - before.usd, 6)
                run["calls"] = after.calls - before.calls
                runs.append(run)
                index += 1
                if progress is not None:
                    progress(index, planned, run)

                if run["status"] == "budget-aborted":
                    aborted = True
                    abort_reason = run.get("error") or "The budget was exhausted."
                    break
            if aborted:
                break
        if aborted:
            break

    return {
        "plannedSessions": planned,
        "runs": runs,
        "aborted": aborted,
        "abortReason": abort_reason if aborted else meter.abort_reason,
        "spend": meter.snapshot().as_dict(),
    }
