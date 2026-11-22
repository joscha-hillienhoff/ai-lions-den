"""Turn live runs into the benchmark's run shape, then score and report them.

The scoring, expectations, issue collection and Markdown sections all come from
``backend.benchmark``; this module only converts what the app returned into that shape
and adds the live header: the target, the call count and what happened to the session.
There is no token metering in the app, so the cost is reported as a call count only.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..benchmark.cases import LionCase
from ..benchmark.report import _num, _table, render_report
from ..benchmark.scoring import collect_issues, compute_metrics, evaluate_expectations
from ..personas import INVESTOR_IDS
from .runner import (
    MAX_PITCHES,
    LiveRun,
    Scenario,
    expected_calls,
    worst_case_calls,
)

RUN_KIND = "live-benchmark-run"
SCHEMA_VERSION = 1
RUN_FILE = "live-benchmark.json"
RUN_REPORT_FILE = "live-benchmark.md"

CALLS_NOTE = (
    "The app has no token metering, so cost is reported as a call count: the calls the app "
    "made to the model, retries included. Transient retries inside a call are not visible "
    "from outside, so this is a lower bound of the billed requests."
)


def cases_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _entries(team: dict[str, Any], step: str) -> dict[str, dict[str, Any]]:
    source = team.get(step) or {}
    entries: dict[str, dict[str, Any]] = {}
    for investor_id in INVESTOR_IDS:
        entry = source.get(investor_id) or {}
        entries[investor_id] = {
            "status": entry.get("status"),
            "score": entry.get("score"),
            "text": entry.get("assessment") or entry.get("statement"),
            "question": entry.get("question"),
            "decision": entry.get("decision"),
            "error": entry.get("error"),
        }
    return entries


def _has_output(entries: dict[str, dict[str, Any]]) -> bool:
    return any(entry["status"] in ("done", "failed", "running") for entry in entries.values())


def scenario_record(
    scenario: Scenario, team: dict[str, Any] | None, *, repeat: int = 1
) -> dict[str, Any]:
    """One scenario in the shape ``backend.benchmark.scoring`` reads."""
    case = scenario.case
    variant = scenario.variant
    status = "not-run" if scenario.status == "planned" else scenario.status
    record: dict[str, Any] = {
        "caseId": case.id,
        "caseTier": case.tier,
        "language": case.language,
        "tags": list(case.tags),
        "variantId": variant.id,
        "variantKind": variant.kind,
        "repeat": repeat,
        "teamId": scenario.team_id,
        "teamName": scenario.team_name,
        "status": status,
        "error": scenario.error,
        "calls": scenario.calls.as_dict(),
        "defense": {
            "answers": {investor_id: variant.answers[investor_id] for investor_id in INVESTOR_IDS},
            "oneChange": variant.one_change,
        },
        "round1": None,
        "verdict": None,
        "rank": None,
    }
    if team is None:
        return record
    round1 = _entries(team, "round1")
    if _has_output(round1):
        record["round1"] = {
            "total": sum(entry["score"] or 0 for entry in round1.values()),
            "investors": round1,
        }
    verdict = _entries(team, "verdict")
    if _has_output(verdict):
        record["verdict"] = {
            "total": sum(entry["score"] or 0 for entry in verdict.values()),
            "inCount": sum(1 for entry in verdict.values() if entry["decision"] == "in"),
            "investors": verdict,
        }
    record["rank"] = team.get("rank")
    return record


def run_records(run: LiveRun) -> list[dict[str, Any]]:
    teams = {team.get("id"): team for team in ((run.final_view or {}).get("teams") or [])}
    return [
        scenario_record(scenario, teams.get(scenario.team_id or "")) for scenario in run.scenarios
    ]


def _score(
    cases: list[LionCase],
    records: list[dict[str, Any]],
    *,
    planned: int,
    calls: int,
) -> dict[str, Any]:
    spend = {
        "usd": 0.0,
        "calls": calls,
        "plannedSessions": planned,
        "aborted": False,
        "abortReason": None,
    }
    metrics = compute_metrics(cases, records, spend)
    # Cost in dollars is not measured; the call count replaces it.
    metrics.pop("cost", None)
    metrics["calls"] = {"total": calls, "perSession": _round(calls / planned) if planned else None}
    expectations = evaluate_expectations(cases, metrics, records)
    issues = collect_issues(cases, records, expectations)
    return {"metrics": metrics, "expectations": expectations, "issues": issues}


def _round(value: float) -> float:
    return round(value, 2)


def build_run_payload(
    run: LiveRun,
    *,
    cases: list[LionCase],
    cases_path: Path,
    base_url: str,
    health: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    records = run_records(run)
    pitches = len(run.scenarios)
    calls = run.calls
    scored = _score(cases, records, planned=pitches, calls=calls.total)
    demo = run.mode.get("demo")
    if not isinstance(demo, bool):
        demo = bool(health.get("demo"))
    return {
        "kind": RUN_KIND,
        "schemaVersion": SCHEMA_VERSION,
        "meta": {
            "generatedAt": iso_now(),
            "baseUrl": base_url,
            "juryMode": "demo" if demo else "live",
            "provider": run.mode.get("provider"),
            "model": run.mode.get("model"),
            "pitches": pitches,
            "maxPitches": MAX_PITCHES,
            "casesPath": str(cases_path),
            "casesSha256": cases_digest(cases_path),
            "runDir": str(run_dir),
            "elapsedSeconds": run.timings.get("totalSeconds"),
        },
        "outcome": run.outcome,
        "abortReason": run.abort_reason,
        "stage": run.stage,
        "calls": {
            **calls.as_dict(),
            "expected": expected_calls(pitches),
            "worstCase": worst_case_calls(pitches),
        },
        "timings": run.timings,
        "previousSession": run.previous_session,
        "finalPhase": ((run.final_view or {}).get("session") or {}).get("phase"),
        "ranking": (run.final_view or {}).get("ranking") or [],
        **scored,
        "runs": records,
        "events": run.events,
    }


def _status_label(outcome: str, reason: str | None) -> str:
    return f"{outcome}: {reason}" if reason else outcome


def _teams_table(records: list[dict[str, Any]]) -> list[str]:
    rows = []
    for record in records:
        round1 = record.get("round1") or {}
        verdict = record.get("verdict") or {}
        calls = record.get("calls") or {}
        retries = (calls.get("round1Retry") or 0) + (calls.get("verdictRetry") or 0)
        rows.append(
            [
                record.get("teamName") or "-",
                f"`{record['caseId']}`",
                record["variantId"],
                record["status"],
                calls.get("total", 0),
                retries,
                round1.get("total", "-"),
                verdict.get("total", "-"),
                f"{verdict['inCount']}/4" if "inCount" in verdict else "-",
                record.get("rank") or "-",
            ]
        )
    return _table(
        ["Team", "Case", "Defense", "Status", "Calls", "Retried", "Round 1", "Final", "In", "Rank"],
        rows,
    )


def _short(value: Any, limit: int = 90) -> str:
    text = str(value).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _case_verdict_rows(payload: dict[str, Any]) -> list[list[Any]]:
    """Per case: did the lions meet the expectations in the case file, and what missed."""
    expectations = payload["expectations"]
    rows: list[list[Any]] = []
    order: list[str] = []
    for record in payload["runs"]:
        if record["caseId"] not in order:
            order.append(record["caseId"])
    for case_id in [*order, None]:
        records = [record for record in payload["runs"] if record["caseId"] == case_id]
        checks = [item for item in expectations if item["caseId"] == case_id]
        if case_id is None and not checks:
            continue
        failed = [item for item in checks if item["ok"] is False]
        unevaluated = any(item["ok"] is None for item in checks) or any(
            record["status"] != "done" for record in records
        )
        if failed:
            verdict = "NOT MET"
        elif unevaluated:
            verdict = "not evaluated"
        elif checks:
            verdict = "met"
        else:
            verdict = "no expectations"
        totals = ", ".join(
            f"{record['variantId']}: "
            f"{(record.get('round1') or {}).get('total', '-')} -> "
            f"{(record.get('verdict') or {}).get('total', '-')}"
            for record in records
        )
        missed = "; ".join(
            f"{item['check']} (expected {_short(item['expected'])}, "
            f"actual {_short(item['actual'])})"
            for item in failed
        )
        rows.append(
            [
                f"`{case_id}`" if case_id else "all cases",
                records[0]["caseTier"] if records else "-",
                totals or "-",
                verdict,
                missed or "-",
            ]
        )
    return rows


def render_run_report(payload: dict[str, Any], cases: list[LionCase]) -> str:
    meta = payload["meta"]
    calls = payload["calls"]
    timings = payload["timings"]
    previous = payload.get("previousSession") or {}
    metrics = payload["metrics"]
    sessions = metrics["sessions"]
    retries = calls["round1Retry"] + calls["verdictRetry"]
    if previous.get("archivedId"):
        previous_text = (
            f"{previous.get('teams')} teams in phase '{previous.get('phase')}' were exported "
            f"and archived as `{previous['archivedId']}`, then reset"
        )
    else:
        previous_text = "the session was empty and was reset"
    run_rows = [
        ["Generated", meta["generatedAt"]],
        ["Target", meta["baseUrl"]],
        [
            "Jury",
            f"{meta['juryMode']} ({meta.get('provider') or '-'}, `{meta.get('model') or '-'}`)",
        ],
        ["Outcome", _status_label(payload["outcome"], payload.get("abortReason"))],
        ["Stage reached", f"{payload['stage']} (final phase '{payload.get('finalPhase') or '-'}')"],
        [
            "Pitches",
            f"{meta['pitches']} (limit {meta['maxPitches']}); "
            f"{sessions['completed']} completed of {sessions['planned']} planned",
        ],
        [
            "Jury calls",
            f"{calls['total']} (expected {calls['expected']} without retries, "
            f"{retries} retried, worst case {calls['worstCase']})",
        ],
        [
            "Durations",
            f"round 1 {_num(timings.get('round1Seconds'), 1)}s, "
            f"verdict {_num(timings.get('verdictSeconds'), 1)}s, "
            f"total {_num(timings.get('totalSeconds'), 1)}s",
        ],
        ["Previous session", previous_text],
    ]
    preface = ["## Teams", ""]
    preface.extend(_teams_table(payload["runs"]))
    preface.append("")
    preface.append("## Per case: did the lions meet the expectations?")
    preface.append("")
    preface.extend(
        _table(
            ["Case", "Tier", "Total round 1 -> final (of 40)", "Expectation", "What missed"],
            _case_verdict_rows(payload),
        )
    )
    preface.append("")
    preface.append(
        "The expectations are the ranges and rules in the case file. The worst examples "
        "further down quote the lions' own words for the failures."
    )
    preface.append("")
    preface.append(
        "Repeat consistency is not measured with a single run: the spread and flip columns "
        "below stay empty."
    )
    preface.append("")
    preface.append(CALLS_NOTE)
    if payload["outcome"] != "completed":
        preface.append("")
        preface.append(
            "> Partial run: some pitches did not finish, so the quality checks below are "
            "reported as not evaluated."
        )
    return render_report(
        cases=cases,
        metrics=metrics,
        expectations=payload["expectations"],
        issues=payload["issues"],
        meta=meta,
        title="Live lion benchmark",
        intro=(
            "One session played against a deployed instance over its public HTTP API, "
            "scored with the same deterministic checks as the lion quality benchmark. "
            "No LLM judge is involved."
        ),
        run_rows=run_rows,
        preface=preface,
    )


def write_outputs(run_dir: Path, payload: dict[str, Any], report: str) -> tuple[Path, Path]:
    json_path = run_dir / RUN_FILE
    report_path = run_dir / RUN_REPORT_FILE
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    return json_path, report_path
