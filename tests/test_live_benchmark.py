"""The live lion benchmark, verified in-process against the real app.

The script drives a deployed instance purely over its HTTP API. Here the instance is the
real FastAPI app behind an in-process transport, with a deterministic fake jury injected,
so the limit, the confirmation gate, archive-before-reset, the auto-advance handling,
retries and the partial report run without any network or key.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from backend.benchmark.cases import DEFAULT_CASES_PATH, load_cases
from backend.benchmark.fake import FakeJuryProvider
from backend.jury.orchestrator import JuryOrchestrator
from backend.jury.types import Round1Input, VerdictInput
from backend.live_benchmark.cli import main
from backend.live_benchmark.runner import (
    MAX_PITCHES,
    LimitError,
    LiveSession,
    Settings,
    check_limit,
    plan_scenarios,
)

from .helpers import TEST_MODERATOR_PASSWORD, demo_app

BASE_URL = "http://test"
CASES = load_cases()
FAST = ["--poll-interval", "0.02", "--jury-timeout", "20"]


class RecordingTransport(httpx.AsyncBaseTransport):
    """Delegates to the in-process app and records every request."""

    def __init__(
        self,
        app: FastAPI,
        *,
        fail: Callable[[httpx.Request], bool] | None = None,
        before: Callable[[httpx.Request], Any] | None = None,
    ) -> None:
        self._inner = ASGITransport(app=app)
        self._fail = fail
        self._before = before
        self.requests: list[tuple[str, str, bytes]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.content))
        if self._before is not None:
            await self._before(request)
        if self._fail is not None and self._fail(request):
            return httpx.Response(500, json={"error": "injected failure"})
        return await self._inner.handle_async_request(request)

    def calls(self, method: str, path: str) -> list[dict[str, Any]]:
        return [
            json.loads(body) if body else {}
            for request_method, request_path, body in self.requests
            if request_method == method and request_path == path
        ]

    @property
    def operations(self) -> list[str]:
        return [f"{method} {path}" for method, path, _body in self.requests]


class ScriptedJury(FakeJuryProvider):
    """The tier-aware fake jury with scripted failures and hangs."""

    def __init__(
        self,
        *,
        fail_round1: Callable[[str, str, int], bool] | None = None,
        fail_verdict: Callable[[str, str, int], bool] | None = None,
        hang_round1: set[str] | None = None,
    ) -> None:
        super().__init__(CASES)
        self.fail_round1 = fail_round1
        self.fail_verdict = fail_verdict
        self.hang_round1 = hang_round1 or set()
        self.attempts: Counter[tuple[str, str, str]] = Counter()

    async def evaluate_round1(self, input_value: Round1Input) -> Any:
        startup, investor = input_value.pitch.startup_name, input_value.investor.id
        self.attempts[("round1", startup, investor)] += 1
        if startup in self.hang_round1:
            await asyncio.sleep(3600)
        attempt = self.attempts[("round1", startup, investor)]
        if self.fail_round1 is not None and self.fail_round1(startup, investor, attempt):
            raise RuntimeError("scripted round 1 failure")
        return await super().evaluate_round1(input_value)

    async def evaluate_verdict(self, input_value: VerdictInput) -> Any:
        startup, investor = input_value.pitch.startup_name, input_value.investor.id
        self.attempts[("verdict", startup, investor)] += 1
        attempt = self.attempts[("verdict", startup, investor)]
        if self.fail_verdict is not None and self.fail_verdict(startup, investor, attempt):
            raise RuntimeError("scripted verdict failure")
        return await super().evaluate_verdict(input_value)


def live_app(jury: ScriptedJury | None = None, **env: str) -> FastAPI:
    app = demo_app(**env)
    runtime = app.state.runtime
    provider = jury or ScriptedJury()
    runtime.provider = provider
    runtime.orchestrator = JuryOrchestrator(runtime.store, provider, runtime.config)
    return app


@pytest.fixture(autouse=True)
def moderator_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODERATOR_PASSWORD", TEST_MODERATOR_PASSWORD)


def run_cli(
    transport: httpx.AsyncBaseTransport, out_dir: Path, *arguments: str, fast: bool = True
) -> int:
    argv = ["--base-url", BASE_URL, "--out-dir", str(out_dir), *arguments]
    if fast:
        argv += FAST
    return main(argv, transport=transport)


def only_run_dir(out_dir: Path) -> Path:
    directories = [path for path in out_dir.iterdir() if path.is_dir()]
    assert len(directories) == 1, directories
    return directories[0]


def read_payload(out_dir: Path) -> dict[str, Any]:
    return json.loads((only_run_dir(out_dir) / "live-benchmark.json").read_text(encoding="utf-8"))


def seed_old_session(app: FastAPI) -> None:
    """An earlier session with a team in it, so that the reset has something to archive."""

    async def seed() -> None:
        async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as team:
            response = await team.post("/api/teams/join", json={"name": "Earlier Session Team"})
            assert response.status_code == 200

    asyncio.run(seed())


# The limit and the plan


def test_default_plan_is_the_ten_defense_scenarios_with_neutral_unique_team_names() -> None:
    scenarios = plan_scenarios(CASES)

    assert len(scenarios) == MAX_PITCHES == 10
    names = [scenario.team_name for scenario in scenarios]
    assert len({name.lower() for name in names}) == 10
    # The jury sees the team name, so it must not reveal which defense a team plays.
    for scenario in scenarios:
        assert "fix" not in scenario.team_name.lower()
        assert "dodge" not in scenario.team_name.lower()
    paired = [
        scenario for scenario in scenarios if scenario.case.id == "paired-defense-skillbridge"
    ]
    assert [scenario.variant.kind for scenario in paired] == ["fix", "dodge"]
    assert [scenario.team_name for scenario in paired] == ["SkillBridge", "SkillBridge 2"]


def test_more_than_ten_pitches_are_refused_before_any_request(tmp_path: Path) -> None:
    document = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    extra = dict(document["cases"][0]["variants"][0], id="extra")
    document["cases"][0]["variants"].append(extra)
    cases_path = tmp_path / "eleven.json"
    cases_path.write_text(json.dumps(document), encoding="utf-8")
    transport = RecordingTransport(live_app())

    with pytest.raises(LimitError, match="at most 10"):
        check_limit(load_cases(cases_path))
    code = run_cli(transport, tmp_path / "out", "--cases", str(cases_path), "--confirm")

    assert code == 2
    assert transport.requests == []
    assert not (tmp_path / "out").exists()


def test_only_selects_fewer_cases_and_unknown_ids_are_refused(tmp_path: Path) -> None:
    transport = RecordingTransport(live_app())

    unknown = run_cli(transport, tmp_path / "out", "--only", "no-such-case", "--confirm")

    assert unknown == 2
    assert transport.requests == []


@pytest.mark.parametrize(
    "url", ["ftp://host", "https://user:secret@host", "https://host/?token=abc", "not a url"]
)
def test_unusable_base_urls_are_refused_before_any_request(tmp_path: Path, url: str) -> None:
    transport = RecordingTransport(live_app())

    code = main(
        ["--base-url", url, "--out-dir", str(tmp_path / "out"), "--confirm"], transport=transport
    )

    assert code == 2
    assert transport.requests == []


# The confirmation gate


def test_without_confirm_the_plan_is_printed_and_nothing_is_changed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = live_app()
    seed_old_session(app)
    transport = RecordingTransport(app)

    code = run_cli(transport, tmp_path / "out")

    output = capsys.readouterr().out
    assert code == 2
    # Only the read-only health check was sent: no login, no reset, no team.
    assert transport.operations == ["GET /api/health"]
    assert "target:      http://test" in output
    assert "DEMO" in output
    assert "10 pitches (limit 10)" in output
    assert "80 expected (10 x 8: 4 round 1 + 4 final verdict)" in output
    assert "at most 160" in output
    assert "REPLACES the current session" in output
    assert "Nothing was changed" in output
    assert not (tmp_path / "out").exists()
    assert app.state.runtime.store.read().teams[0].name == "Earlier Session Team"


def test_the_plan_says_when_the_target_runs_the_real_jury(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_health(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "phase": "winner", "teams": 12, "demo": False})

    code = main(
        ["--base-url", "https://azure.example", "--out-dir", str(tmp_path / "out")],
        transport=httpx.MockTransport(live_health),
    )

    output = capsys.readouterr().out
    assert code == 2
    assert "LIVE - real model calls" in output
    assert "phase 'winner', 12 teams" in output
    assert "every call above is billed" in output


def test_an_unreachable_target_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    code = main(
        ["--base-url", "https://down.example", "--out-dir", str(tmp_path / "out"), "--confirm"],
        transport=httpx.MockTransport(down),
    )

    assert code == 2
    assert "cannot read" in capsys.readouterr().err


def test_confirm_without_a_moderator_password_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MODERATOR_PASSWORD")
    transport = RecordingTransport(live_app())

    code = run_cli(transport, tmp_path / "out", "--confirm")

    assert code == 2
    assert "MODERATOR_PASSWORD" in capsys.readouterr().err
    assert transport.operations == ["GET /api/health"]


def test_there_is_no_password_argument() -> None:
    with pytest.raises(SystemExit):
        main(["--base-url", BASE_URL, "--moderator-password", "x"])


# A complete run


def test_full_run_archives_resets_plays_and_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jury = ScriptedJury()
    app = live_app(jury)
    seed_old_session(app)
    transport = RecordingTransport(app)

    code = run_cli(transport, tmp_path / "out", "--confirm")

    assert code == 0, capsys.readouterr()
    payload = read_payload(tmp_path / "out")
    assert payload["outcome"] == "completed"
    assert payload["finalPhase"] == "leaderboard"
    assert payload["calls"]["total"] == payload["calls"]["expected"] == 80
    assert payload["calls"]["round1Retry"] == payload["calls"]["verdictRetry"] == 0
    assert sum(jury.attempts.values()) == 80
    assert len(payload["runs"]) == 10
    assert {run["status"] for run in payload["runs"]} == {"done"}
    assert all(run["verdict"]["total"] >= 4 for run in payload["runs"])
    assert payload["metrics"]["sessions"]["completed"] == 10
    assert not payload["metrics"]["partial"]
    assert payload["metrics"]["discrimination"]["passed"] is True
    assert len(payload["ranking"]) == 10

    # The old session is exported, then archived by the reset, before anything is created.
    operations = transport.operations
    export = operations.index("GET /api/moderator/export")
    reset = operations.index("POST /api/moderator/reset")
    first_join = operations.index("POST /api/teams/join")
    assert export < reset < first_join
    run_dir = only_run_dir(tmp_path / "out")
    exported = json.loads((run_dir / "previous-session-export.json").read_text(encoding="utf-8"))
    assert exported["teams"][0]["name"] == "Earlier Session Team"
    archives = app.state.runtime.store.list_archives()
    assert len(archives) == 1
    assert payload["previousSession"]["archivedId"] == archives[0].id
    assert payload["previousSession"]["teams"] == 1

    # Phases moved forward one at a time, always with expectedPhase, and stopped at the leaderboard.
    advances = [body["expectedPhase"] for body in transport.calls("POST", "/api/moderator/phase")]
    assert advances == ["lobby", "ideation", "jury", "defense", "verdict"]
    forbidden = ("finale", "vote", "reveal", "winner", "phase/back", "phase/hold")
    assert not [op for op in operations if any(word in op for word in forbidden)]

    report = (run_dir / "live-benchmark.md").read_text(encoding="utf-8")
    assert "# Live lion benchmark" in report
    assert "80 (expected 80 without retries, 0 retried" in report
    assert "## Discrimination by tier" in report
    assert "## Defense effect" in report
    assert "no token metering" in report
    assert "## Per case: did the lions meet the expectations?" in report
    assert "Repeat consistency is not measured with a single run" in report

    # The password never reaches an output file or the console.
    everything = capsys.readouterr()
    for text in (
        report,
        json.dumps(payload),
        (run_dir / "previous-session-export.json").read_text(encoding="utf-8"),
        everything.out,
        everything.err,
    ):
        assert TEST_MODERATOR_PASSWORD not in text


def test_teams_play_their_scripted_pitch_and_defense(tmp_path: Path) -> None:
    app = live_app()
    code = run_cli(
        RecordingTransport(app),
        tmp_path / "out",
        "--confirm",
        "--only",
        "paired-defense-skillbridge",
    )

    assert code == 0
    state = app.state.runtime.store.read()
    by_name = {team.name: team for team in state.teams}
    case = next(case for case in CASES if case.id == "paired-defense-skillbridge")
    fix, dodge = case.variants
    assert by_name["SkillBridge"].pitch.problem == case.pitch.problem
    assert by_name["SkillBridge"].defense.one_change == fix.one_change
    assert by_name["SkillBridge 2"].defense.one_change == dodge.one_change
    assert by_name["SkillBridge"].defense.answers[0] == fix.answers["growth"]
    assert by_name["SkillBridge 2"].defense.answers[3] == dodge.answers["technology"]
    assert state.phase == "leaderboard"
    # Fewer pitches, fewer calls.
    assert read_payload(tmp_path / "out")["calls"]["total"] == 16


# The jury round advancing to the defense by itself


async def _play(
    app: FastAPI,
    run_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    **settings: Any,
) -> LiveSession:
    run_dir.mkdir(parents=True, exist_ok=True)
    session = LiveSession(
        base_url=BASE_URL,
        password=TEST_MODERATOR_PASSWORD,
        scenarios=plan_scenarios(
            [case for case in CASES if case.id != "paired-defense-skillbridge"][:3]
        ),
        settings=Settings(poll_interval=0.3, jury_timeout=20, **settings),
        run_dir=run_dir,
        transport=transport or ASGITransport(app=app),
        log=lambda _message: None,
    )
    await session.play()
    return session


async def test_an_automatic_advance_to_the_defense_is_tolerated_and_never_doubled(
    tmp_path: Path,
) -> None:
    app = live_app(JURY_AUTO_ADVANCE_SECONDS="0.05")
    transport = RecordingTransport(app)

    session = await _play(app, tmp_path, transport=transport)

    assert session.run.outcome == "completed"
    messages = [event["message"] for event in session.run.events]
    assert "The jury round already advanced to the defense by itself." in messages
    # The countdown did the jury -> defense step, so the script sent no advance from the jury.
    advances = [body["expectedPhase"] for body in transport.calls("POST", "/api/moderator/phase")]
    assert advances == ["lobby", "ideation", "defense", "verdict"]
    assert app.state.runtime.store.read().phase == "leaderboard"


async def test_start_now_that_loses_the_race_with_the_countdown_is_not_an_error(
    tmp_path: Path,
) -> None:
    app = live_app(JURY_AUTO_ADVANCE_SECONDS="60")
    raced = False

    async def second_moderator_advances_first(request: httpx.Request) -> None:
        nonlocal raced
        if (
            request.url.path == "/api/moderator/phase"
            and b'"jury"' in request.content
            and not raced
        ):
            raced = True
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url=BASE_URL
            ) as other:
                await other.post("/api/moderator/login", json={"password": TEST_MODERATOR_PASSWORD})
                moved = await other.post("/api/moderator/phase", json={"expectedPhase": "jury"})
                assert moved.status_code == 200

    transport = RecordingTransport(app, before=second_moderator_advances_first)

    session = await _play(app, tmp_path, transport=transport)

    assert raced
    assert session.run.outcome == "completed"
    assert "The session was already in 'defense'." in [e["message"] for e in session.run.events]
    # The runner's own advance was rejected by expectedPhase instead of skipping the defense.
    assert app.state.runtime.store.read().phase == "leaderboard"
    assert len(session.run.final_view["teams"]) == 3


# Retries


def test_a_failed_call_is_retried_once_and_the_retry_is_counted(tmp_path: Path) -> None:
    jury = ScriptedJury(
        fail_round1=lambda startup, investor, attempt: (
            startup == "Mindful" and investor == "growth" and attempt == 1
        ),
        fail_verdict=lambda startup, investor, attempt: (
            startup == "PlantTalk" and investor == "finance" and attempt == 1
        ),
    )

    code = run_cli(RecordingTransport(live_app(jury)), tmp_path / "out", "--confirm")

    assert code == 0
    payload = read_payload(tmp_path / "out")
    assert payload["outcome"] == "completed"
    # One failed round 1 call is run again; a verdict retry runs all four verdict calls again.
    assert payload["calls"]["round1Retry"] == 1
    assert payload["calls"]["verdictRetry"] == 4
    assert payload["calls"]["total"] == 80 + 1 + 4
    mindful = next(run for run in payload["runs"] if run["teamName"] == "Mindful")
    assert mindful["calls"]["round1Retry"] == 1
    assert jury.attempts[("round1", "Mindful", "growth")] == 2
    assert jury.attempts[("verdict", "PlantTalk", "finance")] == 2
    assert "85 (expected 80 without retries, 5 retried" in (
        only_run_dir(tmp_path / "out") / "live-benchmark.md"
    ).read_text(encoding="utf-8")


def test_a_team_that_fails_again_after_its_one_retry_gives_a_partial_report(
    tmp_path: Path,
) -> None:
    jury = ScriptedJury(
        fail_round1=lambda startup, investor, _attempt: (
            startup == "Mindful" and investor == "growth"
        )
    )

    code = run_cli(RecordingTransport(live_app(jury)), tmp_path / "out", "--confirm")

    assert code == 1
    payload = read_payload(tmp_path / "out")
    assert payload["outcome"] == "partial"
    assert payload["metrics"]["partial"] is True
    statuses = {run["teamName"]: run["status"] for run in payload["runs"]}
    assert statuses.pop("Mindful") == "round1-failed"
    assert set(statuses.values()) == {"done"}
    # At most one retry per team and step, even though it failed again.
    assert jury.attempts[("round1", "Mindful", "growth")] == 2
    # The failed team never reaches the verdict: 40 + 1 retry + 9 x 4.
    assert payload["calls"]["total"] == 40 + 1 + 36
    assert payload["finalPhase"] == "leaderboard"
    report = (only_run_dir(tmp_path / "out") / "live-benchmark.md").read_text(encoding="utf-8")
    assert "Partial run" in report
    assert "round1-failed" in report


# A timeout


def test_a_timeout_ends_the_run_cleanly_with_a_partial_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    jury = ScriptedJury(hang_round1={"Mindful"})
    app = live_app(jury)
    transport = RecordingTransport(app)

    code = main(
        [
            "--base-url",
            BASE_URL,
            "--out-dir",
            str(tmp_path / "out"),
            "--confirm",
            "--poll-interval",
            "0.05",
            "--jury-timeout",
            "1",
        ],
        transport=transport,
    )

    assert code == 1
    payload = read_payload(tmp_path / "out")
    assert payload["outcome"] == "aborted"
    assert "Timed out after 1s waiting for jury round 1" in payload["abortReason"]
    assert payload["stage"] == "jury-round1"
    assert payload["finalPhase"] == "jury"
    statuses = {run["teamName"]: run["status"] for run in payload["runs"]}
    assert set(statuses.values()) == {"timeout"}
    # The finished round 1 results are still in the report; the run never moved past the jury.
    assert sum(1 for run in payload["runs"] if run["round1"]) == 10
    assert transport.calls("POST", "/api/moderator/phase")[-1]["expectedPhase"] == "ideation"
    assert "Run aborted" in capsys.readouterr().out
    report = (only_run_dir(tmp_path / "out") / "live-benchmark.md").read_text(encoding="utf-8")
    assert "Timed out after 1s" in report


def test_a_failed_export_stops_the_run_before_the_session_is_reset(
    tmp_path: Path,
) -> None:
    app = live_app()
    seed_old_session(app)
    transport = RecordingTransport(
        app, fail=lambda request: request.url.path == "/api/moderator/export"
    )

    code = run_cli(transport, tmp_path / "out", "--confirm")

    assert code == 1
    assert "POST /api/moderator/reset" not in transport.operations
    assert "POST /api/teams/join" not in transport.operations
    # Nothing was lost: the old session is untouched and nothing was archived or created.
    state = app.state.runtime.store.read()
    assert [team.name for team in state.teams] == ["Earlier Session Team"]
    payload = read_payload(tmp_path / "out")
    assert payload["outcome"] == "aborted"
    assert {run["status"] for run in payload["runs"]} == {"not-run"}


def test_a_wrong_password_stops_the_run_without_touching_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("MODERATOR_PASSWORD", "not-the-password")
    app = live_app()
    seed_old_session(app)
    transport = RecordingTransport(app)

    code = run_cli(transport, tmp_path / "out", "--confirm")

    assert code == 1
    assert "POST /api/moderator/reset" not in transport.operations
    assert [team.name for team in app.state.runtime.store.read().teams] == ["Earlier Session Team"]
    everything = capsys.readouterr()
    assert "not-the-password" not in everything.out + everything.err
    assert "not-the-password" not in json.dumps(read_payload(tmp_path / "out"))


def test_the_report_says_per_case_whether_the_lions_met_the_expectations(tmp_path: Path) -> None:
    jury = ScriptedJury()
    # A vague pitch that the jury scores far too high must show up as a missed expectation.
    jury._score_by_startup["Mindful"] = 9

    code = run_cli(RecordingTransport(live_app(jury)), tmp_path / "out", "--confirm")

    assert code == 0
    payload = read_payload(tmp_path / "out")
    report = (only_run_dir(tmp_path / "out") / "live-benchmark.md").read_text(encoding="utf-8")
    weak = next(
        line
        for line in report.splitlines()
        if line.startswith("| `weak-vague-wellness` |") and "NOT MET" in line
    )
    assert "expected" in weak and "actual" in weak
    assert any(
        item["caseId"] == "weak-vague-wellness" and item["ok"] is False
        for item in payload["expectations"]
    )
    assert "| `strong-reusable-packaging` | strong |" in report
    assert "| met |" in report
    assert "## Worst examples" in report


def test_a_partial_run_reports_the_expectations_as_not_evaluated(tmp_path: Path) -> None:
    jury = ScriptedJury(
        fail_round1=lambda startup, investor, _attempt: (
            startup == "Mindful" and investor == "growth"
        )
    )

    run_cli(RecordingTransport(live_app(jury)), tmp_path / "out", "--confirm")

    report = (only_run_dir(tmp_path / "out") / "live-benchmark.md").read_text(encoding="utf-8")
    assert "| not evaluated |" in report
    assert "NOT MET" not in report
