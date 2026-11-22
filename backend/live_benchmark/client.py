"""A thin HTTP client for the deployed app's public API.

Only endpoints the browser views already use are called. The moderator password is
passed to ``login`` once and is never stored on the client, logged or put into an
error message.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

READ_ATTEMPTS = 3
READ_BACKOFF_SECONDS = 0.5


class ApiError(RuntimeError):
    """A request failed or returned an unexpected status."""

    def __init__(self, operation: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{operation}: {message}")
        self.operation = operation
        self.status = status


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("error"), str):
        return payload["error"][:300]
    return response.text[:200].replace("\n", " ")


class Api:
    """One browser-like session: its own cookie jar, one base URL."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        operation: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        accept: tuple[int, ...] = (200,),
        attempts: int = 1,
    ) -> httpx.Response:
        """Send one request. Only reads are retried, and only on transport errors."""
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, path, json=json, params=params)
            except httpx.HTTPError as error:
                if attempt + 1 < attempts:
                    await asyncio.sleep(READ_BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ApiError(operation, f"{type(error).__name__}: {error}") from error
            if response.status_code not in accept:
                raise ApiError(
                    operation,
                    f"HTTP {response.status_code}: {_detail(response)}",
                    response.status_code,
                )
            return response
        raise AssertionError("unreachable")

    async def get_json(self, path: str, operation: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.request("GET", path, operation, attempts=READ_ATTEMPTS, **kwargs)
        try:
            payload = response.json()
        except ValueError as error:
            raise ApiError(operation, "the response is not JSON", response.status_code) from error
        if not isinstance(payload, dict):
            raise ApiError(operation, "expected a JSON object", response.status_code)
        return payload

    async def post_json(
        self, path: str, operation: str, body: Any, **kwargs: Any
    ) -> dict[str, Any]:
        response = await self.request("POST", path, operation, json=body, **kwargs)
        try:
            payload = response.json()
        except ValueError as error:
            raise ApiError(operation, "the response is not JSON", response.status_code) from error
        if not isinstance(payload, dict):
            raise ApiError(operation, "expected a JSON object", response.status_code)
        return payload

    # Public reads

    async def health(self) -> dict[str, Any]:
        return await self.get_json("/api/health", "health")

    # Moderator

    async def login(self, password: str) -> None:
        # The body carries the password; ApiError never echoes it.
        await self.request(
            "POST", "/api/moderator/login", "moderator login", json={"password": password}
        )

    async def moderator_state(self) -> dict[str, Any]:
        return await self.get_json("/api/moderator/state", "moderator state")

    async def export_json(self) -> str:
        response = await self.request(
            "GET",
            "/api/moderator/export",
            "export current session",
            params={"format": "json"},
            attempts=READ_ATTEMPTS,
        )
        return response.text

    async def archive(self, archive_id: str) -> dict[str, Any]:
        return await self.get_json(f"/api/moderator/archives/{archive_id}", "read archive")

    async def reset(self) -> dict[str, Any]:
        return await self.post_json("/api/moderator/reset", "reset session", {"confirm": "RESET"})

    async def advance(self, expected_phase: str) -> dict[str, Any]:
        return await self.post_json(
            "/api/moderator/phase", "advance phase", {"expectedPhase": expected_phase}
        )

    async def retry(self, team_id: str, step: str) -> dict[str, Any]:
        return await self.post_json(
            "/api/moderator/retry", f"retry {step}", {"teamId": team_id, "step": step}
        )

    # Team

    async def join(self, name: str) -> dict[str, Any]:
        return await self.post_json("/api/teams/join", "join team", {"name": name})

    async def submit_pitch(
        self, pitch: dict[str, str], expected_updated_at: int | None
    ) -> dict[str, Any]:
        return await self.post_json(
            "/api/teams/pitch",
            "submit pitch",
            {"pitch": pitch, "submit": True, "expectedPitchUpdatedAt": expected_updated_at},
        )

    async def submit_defense(
        self, answers: list[str], one_change: str, expected_submitted_at: int | None
    ) -> dict[str, Any]:
        return await self.post_json(
            "/api/teams/defense",
            "submit defense",
            {
                "answers": answers,
                "oneChange": one_change,
                "expectedDefenseSubmittedAt": expected_submitted_at,
            },
        )
