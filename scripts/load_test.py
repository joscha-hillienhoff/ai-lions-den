#!/usr/bin/env python3
"""Load test a demo-mode session with 10 teams and 60 voters.

The script talks only through the public HTTP API, so it can run against a
local server or an Azure test instance. It refuses to run unless /api/health
reports demo mode, because a real jury would call a paid model.

Usage:
    uv run python scripts/load_test.py --base-url http://127.0.0.1:3000 --reset
    uv run python scripts/load_test.py --base-url https://demo.example.com --reset

Exit codes: 0 success, 1 load or correctness failure, 2 target is not demo mode.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

INVESTOR_IDS = ("growth", "finance", "customer", "technology")


class LoadTestError(RuntimeError):
    pass


class ControlFailure(LoadTestError):
    pass


@dataclass
class Result:
    operation: str
    milliseconds: float
    status: int | None
    error: str = ""


@dataclass
class CorrectnessFailure:
    operation: str
    error: str


@dataclass
class Metrics:
    results: list[Result] = field(default_factory=list)
    correctness_failures: list[CorrectnessFailure] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self, operation: str, milliseconds: float, status: int | None, error: str = ""
    ) -> None:
        with self._lock:
            self.results.append(Result(operation, milliseconds, status, error))

    def record_correctness(self, operation: str, error: str) -> None:
        with self._lock:
            self.correctness_failures.append(CorrectnessFailure(operation, error))

    def failures(self) -> list[Result]:
        return [result for result in self.results if result.status != 200]

    def summary(self) -> dict[str, Any]:
        values = [result.milliseconds for result in self.results]
        failures = self.failures()
        by_operation: dict[str, list[float]] = {}
        for result in self.results:
            by_operation.setdefault(result.operation, []).append(result.milliseconds)
        return {
            "requests": len(self.results),
            "errors": len(failures),
            "errorRate": round(len(failures) / len(self.results), 4) if self.results else 0.0,
            "latencyMs": {
                "p50": _percentile(values, 0.50),
                "p90": _percentile(values, 0.90),
                "p95": _percentile(values, 0.95),
                "p99": _percentile(values, 0.99),
                "max": round(max(values), 1) if values else 0.0,
            },
            "operations": {
                operation: {
                    "count": len(timings),
                    "p50": _percentile(timings, 0.50),
                    "p95": _percentile(timings, 0.95),
                    "max": round(max(timings), 1),
                }
                for operation, timings in sorted(by_operation.items())
            },
            "failures": [asdict(failure) for failure in failures[:20]],
            "correctnessFailures": [asdict(failure) for failure in self.correctness_failures[:20]],
        }


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return round(ordered[index], 1)


def _pitch(index: int) -> dict[str, str]:
    return {
        "startupName": f"Load Startup {index + 1:02d}",
        "tagline": f"The measured idea number {index + 1:02d}.",
        "problem": f"Customers in segment {index + 1:02d} lose time and money every week.",
        "solution": f"A product that removes that work for customer group {index + 1:02d}.",
        "customers": f"Mid-sized companies in segment {index + 1:02d} in Germany.",
        "businessModel": f"A subscription of {index + 1}00 EUR per month per customer.",
    }


def _defense(index: int) -> dict[str, Any]:
    return {
        "answers": [
            f"Team {index + 1:02d} starts with three pilot customers and measures the result.",
            f"Team {index + 1:02d} charges 900 EUR per month and covers its costs.",
            f"Team {index + 1:02d} has a pilot showing 62 percent local resale.",
            f"Team {index + 1:02d} names its next milestone and the owner.",
        ],
        "oneChange": f"Team {index + 1:02d} narrows the first customer segment.",
    }


def _parallel(function: Any, items: Any, workers: int) -> list[Any]:
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(items)))) as pool:
        return list(pool.map(function, items))


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    operation: str,
    metrics: Metrics,
    *,
    control: bool = False,
    **kwargs: Any,
) -> httpx.Response:
    started = time.perf_counter()
    try:
        response = client.request(method, path, **kwargs)
    except httpx.HTTPError as error:
        metrics.record(operation, (time.perf_counter() - started) * 1000, None, str(error))
        error_type = ControlFailure if control else LoadTestError
        raise error_type(f"{operation}: {error}") from error
    metrics.record(operation, (time.perf_counter() - started) * 1000, response.status_code)
    if control and response.status_code != 200:
        body = response.text[:300].replace("\n", " ")
        raise ControlFailure(f"{operation} returned {response.status_code}: {body}")
    return response


def _require(response: httpx.Response, operation: str, *, control: bool = False) -> httpx.Response:
    if response.status_code != 200:
        body = response.text[:300].replace("\n", " ")
        error_type = ControlFailure if control else LoadTestError
        raise error_type(f"{operation} returned {response.status_code}: {body}")
    return response


def _record_failure(metrics: Metrics, operation: str, message: str) -> None:
    metrics.record_correctness(operation, message)


def _parse_payload(
    response: httpx.Response,
    operation: str,
    metrics: Metrics,
    validator: Any,
) -> Any | None:
    try:
        return validator(response.json())
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:
        message = f"Invalid {operation} response: {error}"
        _record_failure(metrics, operation, message[:300])
        return None


def _object_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("expected a JSON object")
    return payload


def _health_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    if not isinstance(result.get("demo"), bool):
        raise ValueError("demo must be a boolean")
    return result


def _phase_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    session = result.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("phase"), str):
        raise ValueError("session.phase must be a string")
    return result


def _moderator_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    teams = result.get("teams")
    if not isinstance(teams, list):
        raise ValueError("teams must be a list")
    for team in teams:
        if not isinstance(team, dict) or not isinstance(team.get("id"), str):
            raise ValueError("team.id must be a string")
        if "eligible" not in team:
            raise ValueError("team.eligible is required")
        eligible = team["eligible"]
        if eligible is not None and not isinstance(eligible, bool):
            raise ValueError("team.eligible must be boolean or null")
        for field_name in ("round1", "verdict"):
            entries = team.get(field_name)
            if not isinstance(entries, dict):
                raise ValueError(f"team.{field_name} must be an object")
            for investor_id in INVESTOR_IDS:
                entry = entries.get(investor_id)
                if not isinstance(entry, dict) or not isinstance(entry.get("status"), str):
                    raise ValueError(f"team.{field_name}.{investor_id}.status must be a string")
        if "verdictCompletedAt" not in team:
            raise ValueError("team.verdictCompletedAt is required")
        verdict_completed_at = team["verdictCompletedAt"]
        if verdict_completed_at is not None and (
            not isinstance(verdict_completed_at, int) or isinstance(verdict_completed_at, bool)
        ):
            raise ValueError("team.verdictCompletedAt must be an integer or null")
    return result


def _join_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    team = result.get("team")
    if not isinstance(team, dict) or not isinstance(team.get("id"), str):
        raise ValueError("team.id must be a string")
    return result


def _validate_finalists(finalists: Any, allowed_team_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(finalists, list):
        raise ValueError("finalists must be a list")
    if len(finalists) != 3:
        raise ValueError("finalists must contain exactly three entries")
    ids: list[str] = []
    for finalist in finalists:
        if not isinstance(finalist, dict) or not isinstance(finalist.get("teamId"), str):
            raise ValueError("finalist.teamId must be a string")
        ids.append(finalist["teamId"])
    if len(set(ids)) != 3:
        raise ValueError("finalist team IDs must be distinct")
    if not set(ids).issubset(allowed_team_ids):
        raise ValueError("finalist team IDs must belong to joined teams")
    return finalists


def _vote_state_payload(
    payload: Any,
    allowed_team_ids: set[str],
    *,
    expected_open: bool,
    require_audience_votes: bool,
) -> dict[str, Any]:
    result = _object_payload(payload)
    if result.get("open") is not expected_open:
        raise ValueError(f"open must be {str(expected_open).lower()}")
    votes_cast = result.get("votesCast")
    if not isinstance(votes_cast, int) or isinstance(votes_cast, bool) or votes_cast < 0:
        raise ValueError("votesCast must be a nonnegative integer")
    finalists = _validate_finalists(result.get("finalists"), allowed_team_ids)
    for finalist in finalists:
        if "audienceVotes" not in finalist:
            if require_audience_votes:
                raise ValueError("finalist.audienceVotes is required")
            continue
        audience_votes = finalist["audienceVotes"]
        if (
            not isinstance(audience_votes, int)
            or isinstance(audience_votes, bool)
            or audience_votes < 0
        ):
            raise ValueError("finalist.audienceVotes must be a nonnegative integer")
    return result


def _vote_payload(payload: Any, allowed_team_ids: set[str]) -> dict[str, Any]:
    return _vote_state_payload(
        payload,
        allowed_team_ids,
        expected_open=True,
        require_audience_votes=False,
    )


def _reveal_payload(payload: Any, allowed_team_ids: set[str]) -> dict[str, Any]:
    return _vote_state_payload(
        payload,
        allowed_team_ids,
        expected_open=False,
        require_audience_votes=True,
    )


def _screen_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    votes_cast = result.get("votesCast")
    if not isinstance(votes_cast, int) or isinstance(votes_cast, bool) or votes_cast < 0:
        raise ValueError("votesCast must be a nonnegative integer")
    return result


def _team_state_payload(payload: Any) -> dict[str, Any]:
    result = _object_payload(payload)
    team = result.get("team")
    if not isinstance(team, dict):
        raise ValueError("team must be an object")
    if not isinstance(team.get("id"), str):
        raise ValueError("team.id must be a string")
    if not isinstance(team.get("name"), str):
        raise ValueError("team.name must be a string")
    return result


def _voter_state_payload(payload: Any, allowed_team_ids: set[str]) -> dict[str, Any]:
    result = _vote_state_payload(
        payload,
        allowed_team_ids,
        expected_open=True,
        require_audience_votes=False,
    )
    if not isinstance(result.get("hasVoted"), bool):
        raise ValueError("hasVoted must be a boolean")
    return result


def _read_payload(
    client: httpx.Client,
    path: str,
    operation: str,
    metrics: Metrics,
    validator: Any,
    *,
    attempts: int = 3,
    record_failure_on_exhaustion: bool = False,
) -> Any | None:
    for attempt in range(attempts):
        try:
            response = _request(client, "GET", path, operation, metrics)
        except LoadTestError:
            response = None
        if response is not None and response.status_code == 200:
            return _parse_payload(response, operation, metrics, validator)
        if attempt + 1 < attempts:
            time.sleep(0.1)
    if record_failure_on_exhaustion:
        _record_failure(
            metrics,
            operation,
            f"Unable to read {operation} after {attempts} attempts.",
        )
    return None


def _advance(moderator: httpx.Client, expected: str, metrics: Metrics) -> dict[str, Any]:
    current_response = _require(
        _request(
            moderator,
            "GET",
            "/api/moderator/state",
            "moderator state",
            metrics,
            control=True,
        ),
        "moderator state",
        control=True,
    )
    current = _parse_payload(current_response, "moderator phase", metrics, _phase_payload)
    if current is None:
        raise ControlFailure("Invalid moderator state response.")
    response = _require(
        _request(
            moderator,
            "POST",
            "/api/moderator/phase",
            "moderator advance",
            metrics,
            control=True,
            json={"expectedPhase": current["session"]["phase"]},
        ),
        "moderator advance",
        control=True,
    )
    payload = _parse_payload(response, "moderator phase", metrics, _phase_payload)
    if payload is None:
        raise ControlFailure("Invalid moderator phase response.")
    if payload["session"]["phase"] != expected:
        message = f"Expected phase {expected}, server reported {payload['session']['phase']}"
        _record_failure(metrics, "phase correctness", message)
        raise ControlFailure(message)
    return payload


def _moderator_view(moderator: httpx.Client, metrics: Metrics) -> dict[str, Any] | None:
    return _read_payload(
        moderator,
        "/api/moderator/state",
        "moderator state",
        metrics,
        _moderator_payload,
    )


def _wait_for_jury(
    moderator: httpx.Client,
    team_clients: list[httpx.Client],
    screen: httpx.Client,
    metrics: Metrics,
    *,
    required_team_ids: list[str | None],
    verdict: bool,
    timeout_seconds: float,
) -> float:
    started = time.perf_counter()
    deadline = started + timeout_seconds

    def read_team(index: int) -> None:
        _read_payload(
            team_clients[index],
            "/api/team/state",
            "team state",
            metrics,
            _team_state_payload,
        )

    while time.perf_counter() < deadline:
        view = _moderator_view(moderator, metrics)
        if view is None:
            time.sleep(0.5)
            continue
        if (
            _read_payload(
                screen,
                "/api/screen/state",
                "screen state",
                metrics,
                _screen_payload,
            )
            is None
        ):
            time.sleep(0.5)
            continue
        _parallel(read_team, list(range(len(team_clients))), min(16, len(team_clients)))

        teams_by_id = {team["id"]: team for team in view["teams"]}
        missing = [team_id for team_id in required_team_ids if team_id not in teams_by_id]
        if missing:
            message = f"Required teams are missing from the moderator view: {missing}."
            _record_failure(metrics, "jury correctness", message)
            raise LoadTestError(message)
        ineligible = [
            team_id for team_id in required_team_ids if teams_by_id[team_id]["eligible"] is not True
        ]
        if ineligible:
            message = f"Required teams are ineligible: {ineligible}."
            _record_failure(metrics, "jury correctness", message)
            raise LoadTestError(message)
        required_teams = [teams_by_id[team_id] for team_id in required_team_ids]

        if verdict:
            failed = any(
                entry["status"] == "failed"
                for team in required_teams
                for investor_id in INVESTOR_IDS
                for entry in (team["verdict"][investor_id],)
            )
            done = all(team["verdictCompletedAt"] is not None for team in required_teams)
        else:
            failed = any(
                entry["status"] == "failed"
                for team in required_teams
                for investor_id in INVESTOR_IDS
                for entry in (team["round1"][investor_id],)
            )
            done = all(
                all(team["round1"][investor_id]["status"] == "done" for investor_id in INVESTOR_IDS)
                for team in required_teams
            )
        if failed:
            message = "A jury step failed; inspect the moderator view."
            _record_failure(metrics, "jury correctness", message)
            raise LoadTestError(message)
        if done:
            return time.perf_counter() - started
        time.sleep(0.5)

    phase = "final verdict" if verdict else "jury round 1"
    message = f"Timed out after {timeout_seconds:.0f}s waiting for {phase}."
    _record_failure(metrics, "jury correctness", message)
    raise LoadTestError(message)


def _build_report(
    base_url: str,
    metrics: Metrics,
    *,
    teams: int,
    voters: int,
    duration: float,
    jury_seconds: float,
    verdict_seconds: float,
    votes_recorded: int,
    refusal_reason: str | None = None,
    failure_reason: str | None = None,
    exit_code: int | None = None,
    control_failure: bool = False,
) -> dict[str, Any]:
    report = {
        "baseUrl": base_url,
        "teams": teams,
        "voters": voters,
        "durationSeconds": round(duration, 1),
        "juryRoundOneSeconds": round(jury_seconds, 1),
        "finalVerdictSeconds": round(verdict_seconds, 1),
        "votesRecorded": votes_recorded,
        **metrics.summary(),
    }
    if refusal_reason is not None:
        report["refusalReason"] = refusal_reason
    if failure_reason is not None:
        report["failureReason"] = failure_reason
    if exit_code is not None:
        report["exitCode"] = exit_code
    if control_failure:
        report["controlFailure"] = True
    return report


def _write_report(path: Path | None, report: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")


def _print_report(
    base_url: str,
    metrics: Metrics,
    *,
    teams: int,
    voters: int,
    duration: float,
    jury_seconds: float,
    verdict_seconds: float,
    votes_recorded: int,
    failure_reason: str | None = None,
    control_failure: bool = False,
) -> dict[str, Any]:
    summary = metrics.summary()
    print(f"\nLoad test against {base_url} (demo mode confirmed)")
    print(f"Teams: {teams}  Voters: {voters}  Duration: {duration:.1f}s")
    print(f"Jury round 1: {jury_seconds:.1f}s  Final verdict: {verdict_seconds:.1f}s")
    print(
        f"Requests: {summary['requests']}  "
        f"Errors: {summary['errors']} ({summary['errorRate'] * 100:.2f}%)"
    )
    latency = summary["latencyMs"]
    print(
        f"Latency ms: p50 {latency['p50']}  p90 {latency['p90']}  "
        f"p95 {latency['p95']}  p99 {latency['p99']}  max {latency['max']}"
    )
    print("Per operation:")
    for operation, figures in summary["operations"].items():
        print(
            f"  {operation:20s} count {figures['count']:4d}  "
            f"p50 {figures['p50']:7.1f}  p95 {figures['p95']:7.1f}  "
            f"max {figures['max']:7.1f}"
        )
    print(f"Votes recorded: {votes_recorded}/{voters}")
    if summary["failures"]:
        print("First failures:")
        for failure in summary["failures"]:
            print(f"  {failure}")
    if summary["correctnessFailures"]:
        print("Correctness failures:")
        for failure in summary["correctnessFailures"]:
            print(f"  {failure}")

    return _build_report(
        base_url,
        metrics,
        teams=teams,
        voters=voters,
        duration=duration,
        jury_seconds=jury_seconds,
        verdict_seconds=verdict_seconds,
        votes_recorded=votes_recorded,
        failure_reason=failure_reason,
        control_failure=control_failure,
    )


def _exit_for_error_rate(report: dict[str, Any], max_error_rate: float) -> int:
    if report.get("controlFailure") or report["correctnessFailures"]:
        print("\nCorrectness failures detected.", file=sys.stderr)
        return 1
    if report["errorRate"] > max_error_rate:
        print(
            f"\nError rate {report['errorRate'] * 100:.2f}% exceeds {max_error_rate * 100:.2f}%.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3000")
    parser.add_argument("--teams", type=int, default=10)
    parser.add_argument("--voters", type=int, default=60)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument(
        "--moderator-password", default=os.environ.get("MODERATOR_PASSWORD", "change-me")
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="per-request timeout in seconds"
    )
    parser.add_argument("--wait-seconds", type=float, default=180.0, help="max wait per jury phase")
    parser.add_argument("--max-error-rate", type=float, default=0.0)
    parser.add_argument("--reset", action="store_true", help="clear the target session first")
    parser.add_argument("--json", type=Path, help="also write the report to this file")
    args = parser.parse_args(argv)

    if args.teams < 3 or args.teams > 20:
        parser.error("--teams must be between 3 and 20; a session needs three finalists")
    if args.voters < 1:
        parser.error("--voters must be at least 1")

    base_url = args.base_url.rstrip("/")
    metrics = Metrics()
    started = time.perf_counter()

    with httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True) as bootstrap:
        try:
            health_response = _request(bootstrap, "GET", "/api/health", "health", metrics)
        except LoadTestError as error:
            reason = f"health check failed: {error}"
            print(f"Refusing to run: {base_url} {reason}", file=sys.stderr)
            _write_report(
                args.json,
                _build_report(
                    base_url,
                    metrics,
                    teams=args.teams,
                    voters=args.voters,
                    duration=time.perf_counter() - started,
                    jury_seconds=0.0,
                    verdict_seconds=0.0,
                    votes_recorded=0,
                    refusal_reason=reason,
                    exit_code=2,
                ),
            )
            return 2
        if health_response.status_code != 200:
            reason = f"/api/health returned {health_response.status_code}."
            print(f"Refusing to run: {base_url}{reason}", file=sys.stderr)
            _write_report(
                args.json,
                _build_report(
                    base_url,
                    metrics,
                    teams=args.teams,
                    voters=args.voters,
                    duration=time.perf_counter() - started,
                    jury_seconds=0.0,
                    verdict_seconds=0.0,
                    votes_recorded=0,
                    refusal_reason=reason,
                    exit_code=2,
                ),
            )
            return 2
        health = _parse_payload(health_response, "health", metrics, _health_payload)
        if health is None:
            reason = "health response was invalid."
            print(f"Refusing to run: {base_url} {reason}", file=sys.stderr)
            _write_report(
                args.json,
                _build_report(
                    base_url,
                    metrics,
                    teams=args.teams,
                    voters=args.voters,
                    duration=time.perf_counter() - started,
                    jury_seconds=0.0,
                    verdict_seconds=0.0,
                    votes_recorded=0,
                    refusal_reason=reason,
                    exit_code=2,
                ),
            )
            return 2
        if health.get("demo") is not True:
            reason = (
                f"not in demo mode (demo={health.get('demo')!r}). Set DEMO_MODE=true on the "
                "target and clear OPENAI_API_KEY so no paid model is called."
            )
            print(f"Refusing to run: {base_url} is {reason}", file=sys.stderr)
            _write_report(
                args.json,
                _build_report(
                    base_url,
                    metrics,
                    teams=args.teams,
                    voters=args.voters,
                    duration=time.perf_counter() - started,
                    jury_seconds=0.0,
                    verdict_seconds=0.0,
                    votes_recorded=0,
                    refusal_reason=reason,
                    exit_code=2,
                ),
            )
            return 2

    with contextlib.ExitStack() as stack:
        moderator = stack.enter_context(
            httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True)
        )
        screen = stack.enter_context(
            httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True)
        )
        team_clients = [
            stack.enter_context(
                httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True)
            )
            for _ in range(args.teams)
        ]
        voter_clients = [
            stack.enter_context(
                httpx.Client(base_url=base_url, timeout=args.timeout, follow_redirects=True)
            )
            for _ in range(args.voters)
        ]
        jury_seconds = 0.0
        verdict_seconds = 0.0
        votes_recorded = 0

        try:
            _require(
                _request(
                    moderator,
                    "POST",
                    "/api/moderator/login",
                    "moderator login",
                    metrics,
                    control=True,
                    json={"password": args.moderator_password},
                ),
                "moderator login",
                control=True,
            )
            if args.reset:
                _require(
                    _request(
                        moderator,
                        "POST",
                        "/api/moderator/reset",
                        "moderator reset",
                        metrics,
                        control=True,
                        json={"confirm": "RESET"},
                    ),
                    "moderator reset",
                    control=True,
                )

            def join(index: int) -> str | None:
                try:
                    response = _request(
                        team_clients[index],
                        "POST",
                        "/api/teams/join",
                        "team join",
                        metrics,
                        json={"name": f"Load Team {index + 1:02d}"},
                    )
                except LoadTestError as error:
                    _record_failure(
                        metrics,
                        "team join completeness",
                        f"Team {index + 1} join failed: {error}",
                    )
                    return None
                if response.status_code != 200:
                    _record_failure(
                        metrics,
                        "team join completeness",
                        f"Team {index + 1} join returned {response.status_code}.",
                    )
                    return None
                payload = _parse_payload(response, "team join", metrics, _join_payload)
                return str(payload["team"]["id"]) if payload is not None else None

            team_ids = _parallel(join, list(range(args.teams)), args.workers)
            joined_indices = [
                index for index, team_id in enumerate(team_ids) if team_id is not None
            ]
            joined_team_ids = {team_id for team_id in team_ids if team_id is not None}
            _advance(moderator, "ideation", metrics)

            def submit_pitch(index: int) -> bool:
                if team_ids[index] is None:
                    return False
                try:
                    response = _request(
                        team_clients[index],
                        "POST",
                        "/api/teams/pitch",
                        "pitch submit",
                        metrics,
                        json={
                            "pitch": _pitch(index),
                            "submit": True,
                            "expectedPitchUpdatedAt": None,
                        },
                    )
                except LoadTestError as error:
                    _record_failure(
                        metrics,
                        "pitch submission completeness",
                        f"Team {index + 1} pitch failed: {error}",
                    )
                    return False
                if response.status_code != 200:
                    _record_failure(
                        metrics,
                        "pitch submission completeness",
                        f"Team {index + 1} pitch returned {response.status_code}.",
                    )
                    return False
                return True

            pitched = _parallel(submit_pitch, joined_indices, args.workers)
            pitched_indices = [
                index for index, ok in zip(joined_indices, pitched, strict=True) if ok
            ]
            _advance(moderator, "jury", metrics)
            jury_seconds = _wait_for_jury(
                moderator,
                [team_clients[index] for index in pitched_indices],
                screen,
                metrics,
                required_team_ids=[team_ids[index] for index in pitched_indices],
                verdict=False,
                timeout_seconds=args.wait_seconds,
            )

            _advance(moderator, "defense", metrics)

            def submit_defense(index: int) -> bool:
                try:
                    response = _request(
                        team_clients[index],
                        "POST",
                        "/api/teams/defense",
                        "defense submit",
                        metrics,
                        json={
                            **_defense(index),
                            "expectedDefenseSubmittedAt": None,
                        },
                    )
                except LoadTestError as error:
                    _record_failure(
                        metrics,
                        "defense submission completeness",
                        f"Team {index + 1} defense failed: {error}",
                    )
                    return False
                if response.status_code != 200:
                    _record_failure(
                        metrics,
                        "defense submission completeness",
                        f"Team {index + 1} defense returned {response.status_code}.",
                    )
                    return False
                return True

            defenses = _parallel(submit_defense, pitched_indices, args.workers)
            defended_indices = [
                index for index, ok in zip(pitched_indices, defenses, strict=True) if ok
            ]
            _advance(moderator, "verdict", metrics)
            verdict_seconds = _wait_for_jury(
                moderator,
                [team_clients[index] for index in defended_indices],
                screen,
                metrics,
                required_team_ids=[team_ids[index] for index in defended_indices],
                verdict=True,
                timeout_seconds=args.wait_seconds,
            )

            _advance(moderator, "leaderboard", metrics)
            _advance(moderator, "finale", metrics)
            _advance(moderator, "voting", metrics)

            def prepare_voter(index: int) -> bool:
                return (
                    _read_payload(
                        voter_clients[index],
                        "/api/vote/state",
                        "voter state",
                        metrics,
                        lambda payload: _voter_state_payload(payload, joined_team_ids),
                        record_failure_on_exhaustion=True,
                    )
                    is not None
                )

            prepared = _parallel(prepare_voter, list(range(args.voters)), args.workers)
            prepared_indices = [index for index, ok in enumerate(prepared) if ok]
            first_vote_state = _read_payload(
                screen,
                "/api/vote/state",
                "vote state",
                metrics,
                lambda payload: _vote_payload(payload, joined_team_ids),
                record_failure_on_exhaustion=True,
            )
            if first_vote_state is None:
                raise LoadTestError("Unable to read vote state before voting.")
            finalist_ids = [entry["teamId"] for entry in first_vote_state["finalists"]]

            def cast_vote(index: int) -> bool:
                try:
                    response = _request(
                        voter_clients[index],
                        "POST",
                        "/api/vote",
                        "vote",
                        metrics,
                        json={"teamId": finalist_ids[index % len(finalist_ids)]},
                    )
                except LoadTestError as error:
                    _record_failure(
                        metrics,
                        "vote submission completeness",
                        f"Voter {index + 1} vote failed: {error}",
                    )
                    return False
                if response.status_code != 200:
                    _record_failure(
                        metrics,
                        "vote submission completeness",
                        f"Voter {index + 1} vote returned {response.status_code}.",
                    )
                    return False
                return True

            votes = _parallel(cast_vote, prepared_indices, args.workers)
            successful_votes = sum(votes)
            expected_vote_counts = {finalist_id: 0 for finalist_id in finalist_ids}
            for index, successful in zip(prepared_indices, votes, strict=True):
                if successful:
                    expected_vote_counts[finalist_ids[index % len(finalist_ids)]] += 1

            screen_view = _read_payload(
                screen,
                "/api/screen/state",
                "screen state",
                metrics,
                _screen_payload,
                record_failure_on_exhaustion=True,
            )
            if screen_view is None:
                raise LoadTestError("Unable to read screen state after voting.")
            votes_recorded = screen_view["votesCast"]
            if votes_recorded != successful_votes:
                message = (
                    f"Expected {successful_votes} recorded votes, server reports {votes_recorded}."
                )
                _record_failure(metrics, "vote correctness", message)
                raise LoadTestError(message)

            _advance(moderator, "winner", metrics)
            reveal = _read_payload(
                screen,
                "/api/vote/state",
                "vote state",
                metrics,
                lambda payload: _reveal_payload(payload, joined_team_ids),
                record_failure_on_exhaustion=True,
            )
            if reveal is None:
                raise LoadTestError("Unable to read vote state at the reveal.")
            if reveal["open"] is not False:
                message = "Voting is still open in the winner phase."
                _record_failure(metrics, "vote correctness", message)
                raise LoadTestError(message)
            if reveal["votesCast"] != successful_votes:
                message = (
                    f"Expected {successful_votes} revealed votes, server reports "
                    f"{reveal['votesCast']}."
                )
                _record_failure(metrics, "vote correctness", message)
                raise LoadTestError(message)
            revealed_counts = {
                entry["teamId"]: entry["audienceVotes"] for entry in reveal["finalists"]
            }
            if sum(revealed_counts.values()) != successful_votes:
                message = "The reveal does not add up to the submitted votes."
                _record_failure(metrics, "vote correctness", message)
                raise LoadTestError(message)
            if revealed_counts != expected_vote_counts:
                message = (
                    f"The reveal distribution differs from submitted votes: "
                    f"expected {expected_vote_counts}, got {revealed_counts}."
                )
                _record_failure(metrics, "vote correctness", message)
                raise LoadTestError(message)
        except LoadTestError as error:
            print(f"\nLoad test failed: {error}", file=sys.stderr)
            duration = time.perf_counter() - started
            report = _print_report(
                base_url,
                metrics,
                teams=args.teams,
                voters=args.voters,
                duration=duration,
                jury_seconds=jury_seconds,
                verdict_seconds=verdict_seconds,
                votes_recorded=votes_recorded,
                failure_reason=str(error),
                control_failure=isinstance(error, ControlFailure),
            )
            exit_code = _exit_for_error_rate(report, args.max_error_rate)
            report["exitCode"] = exit_code
            _write_report(args.json, report)
            return exit_code

    duration = time.perf_counter() - started
    report = _print_report(
        base_url,
        metrics,
        teams=args.teams,
        voters=args.voters,
        duration=duration,
        jury_seconds=jury_seconds,
        verdict_seconds=verdict_seconds,
        votes_recorded=votes_recorded,
    )
    exit_code = _exit_for_error_rate(report, args.max_error_rate)
    report["exitCode"] = exit_code
    _write_report(args.json, report)
    if exit_code == 0:
        print("\nLoad test passed.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
