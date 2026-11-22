from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from backend.config import AppConfig, load_config
from backend.main import create_app
from backend.personas import InvestorId
from backend.state import (
    Defense,
    Pitch,
    Round1Entry,
    SessionState,
    Team,
    VerdictEntry,
    empty_round1,
    empty_verdict,
)
from backend.store import Store

COMPLETE_PITCH = Pitch(
    startup_name="Loopline",
    tagline="The return button that makes online fashion profitable.",
    problem="Online fashion loses margin on returns and customers hate the process.",
    solution="A one-click return flow that resells returned items locally.",
    customers="Mid-sized European fashion retailers with high return rates.",
    business_model="SaaS subscription plus a fee per resold item.",
)


def make_team(index: int, **overrides: object) -> Team:
    now = 1_700_000_000_000 + index * 1_000
    values: dict[str, object] = {
        "id": f"team-{index}",
        "name": f"Team {index}",
        "token": f"token-{index}",
        "created_at": now,
        "pitch": COMPLETE_PITCH.model_copy(update={"startup_name": f"Startup {index}"}),
        "pitch_updated_at": now,
        "submitted_at": now,
        "eligible": True,
        "round1": empty_round1(),
        "defense": Defense(
            answers=["Answer A", "Answer B", "Answer C", "Answer D"],
            one_change="A concrete change from the feedback.",
            submitted_at=now,
        ),
        "verdict": empty_verdict(),
        "verdict_completed_at": None,
    }
    values.update(overrides)
    return Team.model_validate(values)


def make_state(
    teams: list[Team],
    phase: str = "lobby",
    now: int = 1_700_000_000_000,
    pitch_order_team_ids: list[str] | None = None,
) -> SessionState:
    return SessionState(
        phase=phase,  # type: ignore[arg-type]
        phase_started_at=now,
        created_at=now,
        leaderboard_team_ids=None,
        pitch_order_team_ids=pitch_order_team_ids,
        finale_finalist_index=0,
        revision=1,
        teams=teams,
    )


def set_verdict(
    team: Team,
    scores: dict[InvestorId, int],
    decisions: dict[InvestorId, str],
) -> None:
    for investor_id, score in scores.items():
        team.verdict[investor_id] = VerdictEntry(
            status="done",
            score=score,
            decision=decisions[investor_id],  # type: ignore[arg-type]
            statement=f"Statement from {investor_id}.",
        )
    team.verdict_completed_at = 1_700_000_100_000


TEST_MODERATOR_PASSWORD = "test-password"


def demo_app(**env_overrides: str) -> FastAPI:
    """A real app instance in demo mode with a fast jury, for API-level tests."""
    values = {
        "DEMO_MODE": "true",
        "MODERATOR_PASSWORD": TEST_MODERATOR_PASSWORD,
        "SESSION_DB": ":memory:",
        "DEMO_DELAY_MS": "1",
        "JURY_MAX_CONCURRENCY": "8",
        # The jury round does not advance by itself unless a test asks for it.
        "JURY_AUTO_ADVANCE_SECONDS": "0",
    }
    values.update(env_overrides)
    return create_app(load_config(values))


def api_client(app: FastAPI, **kwargs: object) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=15,
        **kwargs,
    )


@contextlib.asynccontextmanager
async def api_clients(app: FastAPI, count: int) -> AsyncIterator[list[AsyncClient]]:
    clients = [api_client(app) for _ in range(count)]
    try:
        yield clients
    finally:
        for client in clients:
            await client.aclose()


def make_config(**overrides: object) -> AppConfig:
    values: dict[str, object] = {
        "demo": True,
        "api_key": None,
        "base_url": None,
        "model": "test-model",
        "moderator_password": "test-password",
        "moderator_password_configured": True,
        "public_base_url": None,
        "temperature": 0.4,
        "request_timeout_ms": 5_000,
        "max_concurrency": 8,
        "demo_delay_ms": 1,
        "db_path": ":memory:",
        "jury_auto_advance_ms": 0,
    }
    values.update(overrides)
    return AppConfig(**values)  # type: ignore[arg-type]


def new_store() -> Store:
    return Store(":memory:")


def write_state(store: Store, state: SessionState) -> None:
    def apply(current: SessionState) -> None:
        for name in SessionState.model_fields:
            setattr(current, name, getattr(state, name))

    store.update(apply)


async def wait_for(
    predicate: Callable[[], bool], timeout_ms: float = 15_000, interval_ms: float = 10
) -> None:
    deadline = time.time() * 1000 + timeout_ms
    while time.time() * 1000 < deadline:
        if predicate():
            return
        await asyncio.sleep(interval_ms / 1000)
    raise TimeoutError("Timed out waiting for condition.")


async def wait_for_async(
    predicate: Callable[[], Awaitable[bool]],
    timeout_ms: float = 15_000,
    interval_ms: float = 10,
) -> None:
    deadline = time.time() * 1000 + timeout_ms
    while time.time() * 1000 < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval_ms / 1000)
    raise TimeoutError("Timed out waiting for condition.")


def unique_token() -> str:
    return str(uuid.uuid4())


def empty_team(**overrides: object) -> Team:
    values: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "name": "Untitled",
        "token": unique_token(),
        "created_at": int(time.time() * 1000),
        "pitch": Pitch(),
        "pitch_updated_at": None,
        "submitted_at": None,
        "eligible": None,
        "round1": empty_round1(),
        "defense": Defense(),
        "verdict": empty_verdict(),
        "verdict_completed_at": None,
    }
    values.update(overrides)
    return Team.model_validate(values)


def round1_entry(
    status: str,
    score: int | None = None,
    assessment: str | None = None,
    question: str | None = None,
) -> Round1Entry:
    return Round1Entry(
        status=status,  # type: ignore[arg-type]
        score=score,
        assessment=assessment,
        question=question,
    )
