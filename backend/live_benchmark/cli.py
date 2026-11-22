"""Command line entry point for the live lion benchmark.

Play one session (at most 10 pitches) against a deployed instance:

    MODERATOR_PASSWORD=... uv run python -m backend.live_benchmark \\
        --base-url https://<app>.azurewebsites.net --confirm

Without --confirm it only prints the plan and changes nothing.

Exit codes: 0 success, 1 partial or failed run, 2 refused (invalid input, a limit, the
missing confirmation or an unreachable target) before anything was changed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..benchmark.cases import DEFAULT_CASES_PATH, CaseFileError, load_cases, select_cases
from .client import Api, ApiError
from .report import build_run_payload, render_run_report, write_outputs
from .runner import (
    INITIAL_CALLS_PER_PITCH,
    MAX_PITCHES,
    LimitError,
    LiveSession,
    Settings,
    expected_calls,
    plan_scenarios,
    worst_case_calls,
)

DEFAULT_OUT_DIR = "data/benchmark-live"
PASSWORD_VARIABLE = "MODERATOR_PASSWORD"


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.live_benchmark",
        description=(
            "Play one scripted session against a deployed instance over its HTTP API and "
            f"score the lions' answers. The moderator password is read from ${PASSWORD_VARIABLE}."
        ),
    )
    parser.add_argument("--base-url", required=True, help="URL of the deployed app")
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="CASE_ID",
        help="play only these case ids (fewer pitches)",
    )
    parser.add_argument("--cases", default=None, help="path to the benchmark case file")
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"gitignored output directory (default {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--jury-timeout",
        type=float,
        default=600.0,
        help="seconds to wait for each jury step before ending the run (default 600)",
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0, help="seconds between polls (default 2)"
    )
    parser.add_argument(
        "--request-timeout", type=float, default=30.0, help="seconds per request (default 30)"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually replace the target's session and run, after the plan is printed",
    )
    return parser


def _error(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _normalise_base_url(value: str) -> str | None:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    return value.strip().rstrip("/")


def _new_dir(out_dir: Path) -> Path:
    base = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    candidate = out_dir / base
    suffix = 2
    while candidate.exists():
        candidate = out_dir / f"{base}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _print_plan(
    *,
    base_url: str,
    health: dict[str, Any],
    pitches: int,
    case_ids: list[str],
    out_dir: Path,
) -> None:
    demo = health.get("demo") is True
    jury = (
        "DEMO - prepared responses, no model calls, no cost"
        if demo
        else "LIVE - real model calls, billed to the target's model account"
    )
    teams = health.get("teams")
    print("Live lion benchmark: plan")
    print(f"  target:      {base_url}")
    print(f"  jury:        {jury}")
    print(f"  session now: phase '{health.get('phase')}', {teams} teams")
    print(f"  this run:    {pitches} pitches (limit {MAX_PITCHES}), one team each:")
    for case_id in case_ids:
        print(f"               - {case_id}")
    print(
        f"  jury calls:  {expected_calls(pitches)} expected "
        f"({pitches} x {INITIAL_CALLS_PER_PITCH}: 4 round 1 + 4 final verdict), "
        f"at most {worst_case_calls(pitches)} if every team needed one retry per step"
    )
    print(f"  output:      {out_dir}/<timestamp>/")
    print()
    print("WARNING: this REPLACES the current session on the target.")
    print("  The existing session is exported and archived first, then reset. Its teams,")
    print("  pitches and results leave the live views, and connected teams have to rejoin.")
    if not demo:
        print("  The target runs the real jury: every call above is billed.")
    print()


def _summary(payload: dict[str, Any]) -> list[str]:
    metrics = payload["metrics"]
    sessions = metrics["sessions"]
    calls = payload["calls"]
    expectations = payload["expectations"]
    passed = sum(1 for item in expectations if item["ok"] is True)
    evaluated = sum(1 for item in expectations if item["ok"] is not None)
    severities: dict[str, int] = {}
    for issue in payload["issues"]:
        severities[issue["severity"]] = severities.get(issue["severity"], 0) + 1
    discrimination = metrics["discrimination"]
    if metrics["partial"] or not discrimination["measured"] or not discrimination["complete"]:
        discrimination_status = "partial"
    else:
        discrimination_status = "pass" if discrimination["passed"] else "FAIL"
    retries = calls["round1Retry"] + calls["verdictRetry"]
    lines = [
        f"Completed {sessions['completed']} of {sessions['planned']} sessions.",
        f"Jury calls: {calls['total']} ({retries} retried).",
        f"Discrimination: {discrimination_status}.",
        "Expectations: partial (not evaluated)."
        if metrics["partial"]
        else f"Expectations: {passed} of {evaluated} passed.",
        f"Issues: {len(payload['issues'])} ({severities.get('high', 0)} high, "
        f"{severities.get('medium', 0)} medium, {severities.get('low', 0)} low).",
    ]
    return lines


def run_main(argv: list[str], transport: httpx.AsyncBaseTransport | None) -> int:
    args = _run_parser().parse_args(argv)
    if args.jury_timeout <= 0 or args.poll_interval <= 0 or args.request_timeout <= 0:
        return _error("--jury-timeout, --poll-interval and --request-timeout must be positive")
    base_url = _normalise_base_url(args.base_url)
    if base_url is None:
        return _error("--base-url must be an http(s) URL without credentials, query or fragment")

    # The limit is checked before the first request of any kind.
    cases_path = Path(args.cases) if args.cases else DEFAULT_CASES_PATH
    try:
        cases = select_cases(load_cases(cases_path), args.only)
        scenarios = plan_scenarios(cases)
    except (CaseFileError, LimitError) as error:
        return _error(str(error))

    out_dir = Path(args.out_dir)
    health = asyncio.run(_read_health(base_url, args.request_timeout, transport))
    if isinstance(health, str):
        return _error(health)
    _print_plan(
        base_url=base_url,
        health=health,
        pitches=len(scenarios),
        case_ids=[f"{s.case.id}/{s.variant.id}" for s in scenarios],
        out_dir=out_dir,
    )
    if not args.confirm:
        print("Nothing was changed. Re-run with --confirm to replace the session and start.")
        return 2

    password = os.environ.get(PASSWORD_VARIABLE, "")
    if not password:
        return _error(f"set the moderator password in the {PASSWORD_VARIABLE} environment variable")

    run_dir = _new_dir(out_dir)
    settings = Settings(
        poll_interval=args.poll_interval,
        jury_timeout=args.jury_timeout,
        request_timeout=args.request_timeout,
    )
    session = LiveSession(
        base_url=base_url,
        password=password,
        scenarios=scenarios,
        settings=settings,
        run_dir=run_dir,
        transport=transport,
        log=lambda message: print(message, flush=True),
    )
    try:
        run = asyncio.run(session.play())
    except KeyboardInterrupt:
        print("\nInterrupted. The session on the target is left as it is.", file=sys.stderr)
        return 1

    payload = build_run_payload(
        run,
        cases=cases,
        cases_path=cases_path,
        base_url=base_url,
        health=health,
        run_dir=run_dir,
    )
    report = render_run_report(payload, cases)
    json_path, report_path = write_outputs(run_dir, payload, report)

    print()
    for line in _summary(payload):
        print(line)
    if payload["outcome"] != "completed":
        reason = payload.get("abortReason") or "some pitches did not finish"
        print(f"Run {payload['outcome']}: {reason}")
        print(f"The target session is left in phase '{payload.get('finalPhase')}'.")
    print("Wrote:")
    print(f"  {json_path}")
    print(f"  {report_path}")
    return 0 if payload["outcome"] == "completed" else 1


async def _read_health(
    base_url: str, timeout: float, transport: httpx.AsyncBaseTransport | None
) -> dict[str, Any] | str:
    """The target's health, or a refusal message. Reads only."""
    api = Api(base_url, timeout=timeout, transport=transport)
    try:
        health = await api.health()
    except ApiError as error:
        return f"cannot read {base_url}/api/health ({error})"
    finally:
        await api.aclose()
    if not isinstance(health.get("demo"), bool):
        return f"{base_url}/api/health did not report whether the jury is demo or live"
    return health


def main(
    argv: list[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> int:
    return run_main(list(sys.argv[1:] if argv is None else argv), transport)


if __name__ == "__main__":
    raise SystemExit(main())
