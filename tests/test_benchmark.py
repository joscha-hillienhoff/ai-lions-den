from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from backend.benchmark.cases import DEFAULT_CASES_PATH, CaseFileError, load_cases
from backend.benchmark.cli import main
from backend.benchmark.estimate import estimate_plan
from backend.benchmark.fake import FakeJuryProvider
from backend.benchmark.meter import BudgetExceeded, UsageMeter
from backend.benchmark.report import render_report
from backend.benchmark.runner import MeteredJuryProvider, run_benchmark
from backend.benchmark.scoring import collect_issues, compute_metrics, evaluate_expectations
from backend.jury.openai import OpenAiJuryProvider
from backend.jury.pricing import (
    MAX_JURY_COMPLETION_TOKENS,
    MAX_JURY_PROMPT_TOKENS,
    UnknownModelPrice,
    cost_usd,
    price_for,
)
from backend.jury.types import Round1Input
from backend.personas import INVESTOR_IDS, investor_by_id

from .helpers import make_config, make_team

PRICE = price_for("gpt-4.1-mini")
EXPECTED_CALLS_PER_SESSION = 2 * len(INVESTOR_IDS)
# What the meter reserves per call: the largest prompt the guard allows plus the output cap.
RESERVE_USD = cost_usd(PRICE, MAX_JURY_PROMPT_TOKENS, MAX_JURY_COMPLETION_TOKENS)


def _spend(result: dict, meter: UsageMeter) -> dict:
    return {
        **result["spend"],
        "budgetUsd": meter.budget_usd,
        "aborted": bool(result["aborted"]),
        "abortReason": result["abortReason"],
        "plannedSessions": result["plannedSessions"],
    }


def _raw_run(
    case_id: str,
    tier: str,
    round1_total: int,
    verdict_total: int,
    decisions: dict[str, str],
) -> dict:
    return {
        "caseId": case_id,
        "caseTier": tier,
        "variantId": "default",
        "variantKind": "neutral",
        "repeat": 1,
        "status": "done",
        "round1": {
            "total": round1_total,
            "investors": {
                investor_id: {
                    "status": "done",
                    "score": round1_total // 4,
                    "text": "The market evidence is concrete.",
                    "question": "What evidence supports the next step?",
                    "decision": None,
                    "error": None,
                }
                for investor_id in INVESTOR_IDS
            },
        },
        "defense": {"answers": {}, "oneChange": "A measured next step."},
        "verdict": {
            "total": verdict_total,
            "inCount": sum(decision == "in" for decision in decisions.values()),
            "investors": {
                investor_id: {
                    "status": "done",
                    "score": verdict_total // 4,
                    "text": "The evidence is sufficient. I'm in."
                    if decisions[investor_id] == "in"
                    else "The evidence is not sufficient. I'm out.",
                    "question": None,
                    "decision": decisions[investor_id],
                    "error": None,
                }
                for investor_id in INVESTOR_IDS
            },
        },
        "error": None,
    }


def test_case_file_rejects_invalid_numeric_expectations(tmp_path) -> None:
    payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    paired = next(case for case in payload["cases"] if case["id"] == "paired-defense-skillbridge")
    paired["expect"]["contrast"]["minGap"] = "not-a-number"
    path = tmp_path / "invalid-cases.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CaseFileError):
        load_cases(path)

    huge = 10**4_000
    for name, mutate in (
        (
            "defense",
            lambda data: data["cases"][0]["variants"][0]["expect"].update(defenseDeltaMin=huge),
        ),
        (
            "contrast",
            lambda data: next(
                case for case in data["cases"] if case["id"] == "paired-defense-skillbridge"
            )["expect"]["contrast"].update(minGap=huge),
        ),
        (
            "total",
            lambda data: data["cases"][0]["expect"].update(meanTotalMin=huge),
        ),
        (
            "rate",
            lambda data: data["cases"][0]["expect"].update(inRateMin=huge),
        ),
    ):
        huge_payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
        mutate(huge_payload)
        huge_path = tmp_path / f"huge-{name}.json"
        huge_path.write_text(json.dumps(huge_payload), encoding="utf-8")
        with pytest.raises(CaseFileError):
            load_cases(huge_path)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("startupName", 60),
        ("tagline", 140),
        ("problem", 2000),
        ("solution", 2000),
        ("customers", 1200),
        ("businessModel", 2000),
    ],
)
def test_case_file_enforces_the_app_pitch_field_limits(tmp_path, field, limit) -> None:
    payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
    payload["cases"][0]["pitch"][field] = "x" * limit
    at_limit = tmp_path / "at-limit.json"
    at_limit.write_text(json.dumps(payload), encoding="utf-8")
    assert len(load_cases(at_limit)) == len(payload["cases"])

    payload["cases"][0]["pitch"][field] = "x" * (limit + 1)
    over_limit = tmp_path / "over-limit.json"
    over_limit.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CaseFileError, match=field):
        load_cases(over_limit)


def test_case_file_rejects_invalid_expectation_semantics(tmp_path) -> None:
    def assert_rejected(name: str, mutate) -> None:
        payload = json.loads(DEFAULT_CASES_PATH.read_text(encoding="utf-8"))
        mutate(payload)
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(CaseFileError):
            load_cases(path)

    assert_rejected(
        "negative-gap.json",
        lambda payload: payload["cases"][7]["expect"]["contrast"].update(minGap=-1),
    )
    assert_rejected(
        "unknown-variant.json",
        lambda payload: payload["cases"][7]["expect"]["contrast"].update(betterVariant="missing"),
    )
    assert_rejected(
        "reversed-defense-bounds.json",
        lambda payload: payload["cases"][0]["variants"][0]["expect"].update(defenseDeltaMax=0),
    )
    assert_rejected(
        "impossible-defense-bound.json",
        lambda payload: payload["cases"][0]["variants"][0]["expect"].update(defenseDeltaMin=9),
    )
    assert_rejected(
        "impossible-gap.json",
        lambda payload: payload["cases"][7]["expect"]["contrast"].update(minGap=17),
    )
    assert_rejected(
        "invalid-rate.json",
        lambda payload: payload["cases"][1]["expect"].update(inRateMin=1.1),
    )
    assert_rejected(
        "reversed-total-bounds.json",
        lambda payload: payload["cases"][2]["expect"].update(meanTotalMin=30, meanTotalMax=20),
    )


def test_case_file_is_targeted_and_machine_readable() -> None:
    cases = load_cases()
    assert DEFAULT_CASES_PATH.name == "lion_quality_cases.json"
    assert len(cases) == 9

    tiers = {case.tier for case in cases}
    assert tiers == {"strong", "mixed", "weak", "one-sided", "troll", "manipulation"}
    assert {case.language for case in cases} == {"en", "de"}

    manipulation = next(case for case in cases if case.tier == "manipulation")
    assert "10/10" in manipulation.expectation.forbidden_phrases
    assert "ignore" in manipulation.pitch.business_model.lower()

    paired = [case for case in cases if len(case.variants) > 1]
    assert len(paired) == 1
    pair = paired[0]
    assert {variant.kind for variant in pair.variants} == {"fix", "dodge"}
    assert pair.expectation.contrast is not None
    assert pair.expectation.contrast.better_variant == "fix"
    assert pair.expectation.contrast.worse_variant == "dodge"

    for case in cases:
        assert case.expectation.mean_total_min is not None or (
            case.expectation.mean_total_max is not None
        )
        for variant in case.variants:
            assert set(variant.answers) == set(INVESTOR_IDS)
            assert variant.one_change.strip()


def test_estimate_counts_every_call_before_a_paid_run() -> None:
    cases = load_cases()
    estimate = estimate_plan(cases, repeats=3, price=PRICE)
    assert estimate.sessions == 10 * 3
    assert estimate.calls == estimate.sessions * EXPECTED_CALLS_PER_SESSION
    assert estimate.prompt_tokens > 0
    assert estimate.completion_tokens > 0
    assert estimate.usd > 0

    single = estimate_plan(cases, repeats=1, price=PRICE)
    assert single.sessions == 10
    assert single.calls == 80
    assert single.usd * 3 == pytest.approx(estimate.usd)


def test_meter_refuses_a_call_without_budget_headroom() -> None:
    meter = UsageMeter(budget_usd=0.001, price=PRICE)
    with pytest.raises(BudgetExceeded):
        meter.before_call()
    assert meter.aborted is True
    assert meter.calls == 0
    assert meter.spent_usd == 0


def test_meter_records_real_usage_and_spend() -> None:
    meter = UsageMeter(budget_usd=1.0, price=PRICE)
    meter.record(1_000, 200)
    meter.record(500, 100)
    snapshot = meter.snapshot()
    assert snapshot.calls == 2
    assert snapshot.prompt_tokens == 1_500
    assert snapshot.completion_tokens == 300
    assert snapshot.usd == pytest.approx(0.00108)


def test_meter_reserves_concurrent_calls_and_charges_missing_usage() -> None:
    meter = UsageMeter(budget_usd=RESERVE_USD * 2.4, price=PRICE)
    expected_reserve_usd = RESERVE_USD
    expected_calls = 2
    for _ in range(expected_calls):
        meter.before_call()
    with pytest.raises(BudgetExceeded):
        meter.before_call()

    for _ in range(expected_calls):
        meter.record(None, None)
    snapshot = meter.snapshot()
    assert snapshot.calls == expected_calls
    assert snapshot.usd == pytest.approx(expected_calls * expected_reserve_usd)
    assert snapshot.prompt_tokens == expected_calls * MAX_JURY_PROMPT_TOKENS
    assert snapshot.completion_tokens == expected_calls * MAX_JURY_COMPLETION_TOKENS


def test_meter_releases_unsent_reservation_without_counting_a_call() -> None:
    meter = UsageMeter(budget_usd=0.01, price=PRICE)
    meter.before_call()
    meter.release()

    assert meter.calls == 0
    assert meter.spent_usd == 0
    assert meter.snapshot().prompt_tokens == 0


def test_meter_keeps_failed_reservation_when_retrying() -> None:
    meter = UsageMeter(budget_usd=RESERVE_USD * 2.4, price=PRICE)
    meter.before_call()
    meter.retry()

    assert meter.calls == 2
    assert meter.spent_usd == pytest.approx(2 * RESERVE_USD)
    meter.record(1, 1)
    assert meter.calls == 2
    assert meter.spent_usd == pytest.approx(RESERVE_USD + cost_usd(PRICE, 1, 1))


def test_meter_preserves_reported_usage_within_the_reservation() -> None:
    meter = UsageMeter(budget_usd=RESERVE_USD * 1.2, price=PRICE)
    meter.before_call()
    meter.record(8_000, 180)

    snapshot = meter.snapshot()
    assert snapshot.usd == pytest.approx(0.003488)
    assert snapshot.prompt_tokens == 8_000
    assert snapshot.completion_tokens == 180
    assert meter.aborted is False


def test_meter_requires_a_finite_budget_below_five() -> None:
    for budget in (float("nan"), float("inf"), 5.0):
        with pytest.raises(ValueError):
            UsageMeter(budget_usd=budget, price=PRICE)


@pytest.mark.parametrize(
    ("model", "repeats", "calls"),
    [("gpt-4.1-mini", 3, 240), ("gpt-4.1", 2, 160)],
)
def test_documented_plans_reservation_stays_under_default_cap(
    model: str, repeats: int, calls: int
) -> None:
    price = price_for(model)
    plan = estimate_plan(load_cases(), repeats, price)
    meter = UsageMeter(budget_usd=4.0, price=price)
    for _ in range(plan.calls):
        meter.before_call()
        meter.record(plan.prompt_tokens // plan.calls, plan.completion_tokens // plan.calls)
    assert plan.calls == calls
    assert meter.aborted is False
    assert meter.spent_usd < 4.0


async def test_meter_closes_a_terminal_provider_failure() -> None:
    class FailingProvider:
        name = "fake"

        def __init__(self) -> None:
            self.sent = False

        async def evaluate_round1(self, input_value):
            self.sent = True
            raise RuntimeError("failed")

        async def evaluate_verdict(self, input_value):
            self.sent = True
            raise RuntimeError("failed")

    meter = UsageMeter(budget_usd=RESERVE_USD * 2.4, price=PRICE)
    failing_provider = FailingProvider()
    provider = MeteredJuryProvider(failing_provider, meter)

    with pytest.raises(RuntimeError):
        await provider.evaluate_round1(
            Round1Input(
                "team-1",
                "Team 1",
                make_team(1).pitch,
                investor_by_id(INVESTOR_IDS[0]),
            )
        )

    assert failing_provider.sent is True
    assert meter.calls == 1
    assert meter.spent_usd == pytest.approx(RESERVE_USD)


def test_unknown_model_price_is_rejected() -> None:
    with pytest.raises(UnknownModelPrice):
        price_for("not-a-real-model")
    with pytest.raises(UnknownModelPrice):
        price_for("gpt-4.1-mini-production")
    assert price_for("gpt-4.1-2025-04-14") == price_for("gpt-4.1")


async def test_openai_provider_reports_usage_for_the_budget() -> None:
    seen: list[tuple[int, int]] = []

    class Completions:
        async def create(self, **arguments):
            content = (
                '{"score": 7, "assessment": "A solid pitch with one open risk.", '
                '"question": "Why now?"}'
                if arguments["response_format"]["json_schema"]["name"] == "jury_round1"
                else '{"score": 8, "statement": "The answer is concrete."}'
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(refusal=None, content=content))],
                usage=SimpleNamespace(prompt_tokens=1_234, completion_tokens=167),
            )

    provider = OpenAiJuryProvider(
        make_config(api_key="test-key", demo=False),
        on_usage=lambda prompt, completion: seen.append((prompt, completion)),
        benchmark_limits=True,
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    investor = investor_by_id(INVESTOR_IDS[0])
    await provider.evaluate_round1(Round1Input("team-1", "Team 1", make_team(1).pitch, investor))

    assert seen == [(1_234, 167)]


async def test_openai_provider_meters_a_temperature_retry() -> None:
    retries: list[bool] = []
    usage: list[tuple[int | None, int | None]] = []
    requests: list[dict] = []

    class TemperatureError(Exception):
        status_code = 400
        message = "temperature is not supported"

    class Completions:
        calls = 0

        async def create(self, **arguments):
            self.calls += 1
            requests.append(arguments.copy())
            if self.calls == 1:
                raise TemperatureError()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            refusal=None,
                            content='{"score": 7, "assessment": "Solid.", "question": "Why now?"}',
                        )
                    )
                ],
                usage=None,
            )

    completions = Completions()
    provider = OpenAiJuryProvider(
        make_config(api_key="test-key", demo=False),
        on_usage=lambda prompt, completion: usage.append((prompt, completion)),
        before_retry=lambda: retries.append(True),
        benchmark_limits=True,
    )
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    investor = investor_by_id(INVESTOR_IDS[0])
    input_value = Round1Input("team-1", "Team 1", make_team(1).pitch, investor)
    await provider.evaluate_round1(input_value)
    await provider.evaluate_round1(input_value)

    assert completions.calls == 3
    assert "temperature" in requests[0]
    assert all("temperature" not in request for request in requests[1:])
    assert retries == [True]
    assert usage == [(None, None), (None, None)]


async def test_full_pipeline_with_fake_client() -> None:
    cases = load_cases()
    meter = UsageMeter(budget_usd=1.0, price=PRICE)
    provider = FakeJuryProvider(cases, on_usage=meter.record)

    result = await run_benchmark(
        cases=cases,
        repeats=1,
        provider=provider,
        meter=meter,
        config=make_config(),
    )

    assert result["aborted"] is False
    assert result["plannedSessions"] == 10
    assert len(result["runs"]) == 10
    assert {run["status"] for run in result["runs"]} == {"done"}
    assert result["spend"]["calls"] == 80
    assert result["spend"]["usd"] > 0
    assert provider.calls == 80

    spend = _spend(result, meter)
    metrics = compute_metrics(cases, result["runs"], spend)
    expectations = evaluate_expectations(cases, metrics, result["runs"])
    issues = collect_issues(cases, result["runs"], expectations)

    assert metrics["sessions"]["completed"] == 10
    assert metrics["sessions"]["failed"] == 0
    assert metrics["cost"]["calls"] == 80
    assert metrics["cost"]["perSessionUsd"] is not None
    assert len(expectations) > 0

    report = render_report(
        cases=cases,
        metrics=metrics,
        expectations=expectations,
        issues=issues,
        meta={"generatedAt": "test", "model": "fake", "priceModel": "gpt-4.1-mini"},
    )
    assert "# Lion quality benchmark" in report
    assert "Discrimination" in report
    assert "Expectations" in report
    assert "Worst examples" in report
    assert "Next step" in report
    assert "in-rate" in report


def test_tier_metrics_weight_each_pitch_once_across_variants() -> None:
    cases = load_cases()
    paired = next(case for case in cases if case.id == "paired-defense-skillbridge")
    single = replace(
        next(case for case in cases if case.id == "mixed-flexibility"),
        id="mixed-single-pitch",
    )
    decisions_in = {investor_id: "in" for investor_id in INVESTOR_IDS}
    decisions_out = {investor_id: "out" for investor_id in INVESTOR_IDS}
    runs = [
        _raw_run(paired.id, paired.tier, 10, 10, decisions_in) | {"variantId": "fix"},
        _raw_run(paired.id, paired.tier, 30, 30, decisions_out) | {"variantId": "dodge"},
        _raw_run(single.id, single.tier, 40, 40, decisions_in),
    ]

    metrics = compute_metrics(
        [paired, single],
        runs,
        {"usd": 0.0, "plannedSessions": 3, "aborted": False, "abortReason": None},
    )

    assert metrics["tierMeans"]["mixed"] == 30.0
    assert metrics["tierInRates"]["mixed"] == 0.75


def test_scoring_uses_literal_metrics_from_a_raw_run_fixture() -> None:
    cases = load_cases()
    selected = [
        case
        for case in cases
        if case.id in {"strong-reusable-packaging", "mixed-flexibility", "weak-vague-wellness"}
    ]
    in_decisions = {investor_id: "in" for investor_id in INVESTOR_IDS}
    out_decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    runs = [
        _raw_run("strong-reusable-packaging", "strong", 28, 32, in_decisions),
        _raw_run("mixed-flexibility", "mixed", 20, 24, out_decisions),
        _raw_run("weak-vague-wellness", "weak", 12, 16, out_decisions),
    ]
    spend = {
        "usd": 0.00123,
        "budgetUsd": 1.0,
        "calls": 24,
        "promptTokens": 12000,
        "completionTokens": 2400,
        "plannedSessions": 3,
        "aborted": False,
        "abortReason": None,
    }

    metrics = compute_metrics(selected, runs, spend)

    assert metrics["tierMeans"] == {"mixed": 24.0, "strong": 32.0, "weak": 16.0}
    assert metrics["overall"]["meanRound1Total"] == 20.0
    assert metrics["overall"]["meanVerdictTotal"] == 24.0
    assert metrics["overall"]["meanDelta"] == 4.0
    assert metrics["isIn"]["rate"] == pytest.approx(1 / 3, abs=0.01)
    assert metrics["cost"]["spendUsd"] == 0.00123
    assert metrics["cost"]["calls"] == 24
    assert metrics["cost"]["perSessionUsd"] == pytest.approx(0.00041)
    assert metrics["discrimination"]["complete"] is False
    assert metrics["discrimination"]["passed"] is None
    report = render_report(
        cases=selected,
        metrics=metrics,
        expectations=[],
        issues=[],
        meta={},
    )
    assert "| Discrimination | partial |" in report
    assert "missing: manipulation, one-sided, troll" in report


def test_incomplete_sessions_mark_quality_metrics_partial() -> None:
    case = next(case for case in load_cases() if case.id == "strong-reusable-packaging")
    decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    run = _raw_run(case.id, case.tier, 20, 20, decisions)
    spend = {"usd": 0.0, "plannedSessions": 2, "aborted": False, "abortReason": None}

    metrics = compute_metrics([case], [run], spend)
    report = render_report(
        cases=[case],
        metrics=metrics,
        expectations=[],
        issues=[],
        meta={},
    )

    assert metrics["partial"] is True
    assert metrics["sessions"]["completed"] == 1
    assert "> Partial benchmark:" in report
    assert "| Discrimination | partial |" in report
    assert "| Faithfulness | partial:" in report


def test_partial_run_never_reports_quality_passes() -> None:
    cases = load_cases()
    tier_totals = {
        "strong": 36,
        "mixed": 28,
        "one-sided": 20,
        "weak": 14,
        "troll": 6,
        "manipulation": 6,
    }
    decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    runs = [
        _raw_run(case.id, case.tier, tier_totals[case.tier], tier_totals[case.tier], decisions)
        | {"variantId": variant.id, "variantKind": variant.kind}
        for case in cases
        for variant in case.variants
    ]
    spend = {
        "usd": 0.0,
        "plannedSessions": len(runs),
        "aborted": False,
        "abortReason": None,
    }

    complete = compute_metrics(cases, runs, spend)
    assert complete["partial"] is False
    assert complete["discrimination"]["passed"] is True

    missing_last = compute_metrics(cases, runs[:-1], spend)
    partial_expectations = evaluate_expectations(cases, missing_last, runs[:-1])
    assert missing_last["partial"] is True
    assert missing_last["discrimination"]["passed"] is None
    assert all(check["ok"] is None for check in missing_last["discrimination"]["order"])
    assert all(pair["ok"] is None for pair in missing_last["defense"]["pairs"])
    assert missing_last["manipulation"]["resisted"] is None
    assert len(partial_expectations) > 0
    assert all(check["ok"] is None for check in partial_expectations)
    assert collect_issues(cases, runs[:-1], partial_expectations) == []
    partial_report = render_report(
        cases=cases,
        metrics=missing_last,
        expectations=partial_expectations,
        issues=[],
        meta={},
    )
    assert "Partial benchmark: checks were not evaluated." in partial_report
    assert "checks passed" not in partial_report
    assert "| Expectations | partial |" in partial_report

    failed_run = {**runs[-1], "status": "failed"}
    with_failure = compute_metrics(cases, [*runs[:-1], failed_run], spend)
    assert with_failure["partial"] is True
    assert with_failure["discrimination"]["passed"] is None
    assert all(
        check["ok"] is None
        for check in evaluate_expectations(cases, with_failure, [*runs[:-1], failed_run])
    )


def test_faithfulness_allows_deal_constants() -> None:
    case = next(case for case in load_cases() if case.id == "strong-reusable-packaging")
    decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    bare_ten = _raw_run(case.id, case.tier, 20, 20, decisions)
    bare_ten["round1"]["investors"]["growth"]["text"] = "The pitch has 10 customers."
    equity = _raw_run(case.id, case.tier, 20, 20, decisions)
    equity["round1"]["investors"]["growth"]["text"] = "The terms use 10 percent equity."
    mixed = _raw_run(case.id, case.tier, 20, 20, decisions)
    mixed["round1"]["investors"]["growth"]["text"] = (
        "The pitch has 10 customers and offers 10 percent equity."
    )
    unrelated_percent = _raw_run(case.id, case.tier, 20, 20, decisions)
    unrelated_percent["round1"]["investors"]["growth"]["text"] = "Churn fell to 10 percent."
    backward_equity = _raw_run(case.id, case.tier, 20, 20, decisions)
    backward_equity["round1"]["investors"]["growth"]["text"] = (
        "The terms include equity of 10 customers."
    )
    spend = {"usd": 0.0, "plannedSessions": 1, "aborted": False, "abortReason": None}

    bare_metrics = compute_metrics([case], [bare_ten], spend)
    equity_metrics = compute_metrics([case], [equity], spend)
    mixed_metrics = compute_metrics([case], [mixed], spend)
    unrelated_metrics = compute_metrics([case], [unrelated_percent], spend)
    backward_metrics = compute_metrics([case], [backward_equity], spend)

    assert bare_metrics["faithfulness"]["unsupported"] == []
    assert equity_metrics["faithfulness"]["unsupported"] == []
    assert mixed_metrics["faithfulness"]["unsupported"] == []
    assert unrelated_metrics["faithfulness"]["unsupported"] == []
    assert backward_metrics["faithfulness"]["unsupported"] == []

    amount_case = next(case for case in load_cases() if case.id == "weak-vague-wellness")
    deal_amount = _raw_run(amount_case.id, amount_case.tier, 20, 20, decisions)
    deal_amount["round1"]["investors"]["growth"]["text"] = (
        "The jury invests 250,000 EUR for 10 percent equity."
    )
    deal_amount_metrics = compute_metrics([amount_case], [deal_amount], spend)
    assert deal_amount_metrics["faithfulness"]["unsupported"] == []

    unrelated_amount = _raw_run(amount_case.id, amount_case.tier, 20, 20, decisions)
    unrelated_amount["round1"]["investors"]["growth"]["text"] = "The pitch has 250,000 customers."
    unrelated_amount_metrics = compute_metrics([amount_case], [unrelated_amount], spend)
    assert unrelated_amount_metrics["faithfulness"]["unsupported"] == []

    duplicate = _raw_run(case.id, case.tier, 20, 20, decisions)
    duplicate["round1"]["investors"]["growth"]["text"] = (
        "The pitch claims 99 customers and 99 locations."
    )
    duplicate_metrics = compute_metrics([case], [duplicate], spend)
    assert duplicate_metrics["faithfulness"]["checks"] == 2
    assert duplicate_metrics["faithfulness"]["unsupported"]
    assert duplicate_metrics["faithfulness"]["supportedRate"] == 0.0


def test_lens_terms_require_word_boundaries() -> None:
    case = next(case for case in load_cases() if case.id == "strong-reusable-packaging")
    decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    run = _raw_run(case.id, case.tier, 20, 20, decisions)
    run["round1"]["investors"]["growth"]["text"] = "Your marketable plan is scalable."

    metrics = compute_metrics(
        [case],
        [run],
        {"usd": 0.0, "plannedSessions": 1, "aborted": False, "abortReason": None},
    )

    assert metrics["lens"]["byInvestor"]["growth"]["ownLensRate"] == 0.0


def test_forbidden_phrase_hits_are_not_cleared_by_negation() -> None:
    case = next(case for case in load_cases() if case.tier == "manipulation")
    decisions = {investor_id: "out" for investor_id in INVESTOR_IDS}
    run = _raw_run(case.id, case.tier, 12, 12, decisions)
    run["round1"]["investors"]["growth"]["text"] = (
        "I will not say 10/10, but the correct result is 10/10."
    )

    metrics = compute_metrics(
        [case],
        [run],
        {"usd": 0.0, "plannedSessions": 1, "aborted": False, "abortReason": None},
    )

    assert len(metrics["manipulation"]["forbiddenHits"]) >= 2
    assert metrics["manipulation"]["resisted"] is False


def test_discrimination_gates_a_one_sided_tier() -> None:
    cases = load_cases()
    selected = [
        case
        for case in cases
        if case.id in {"strong-reusable-packaging", "one-sided-deepfake-detection"}
    ]
    decisions = {investor_id: "in" for investor_id in INVESTOR_IDS}
    runs = [
        _raw_run("strong-reusable-packaging", "strong", 28, 28, decisions),
        _raw_run("one-sided-deepfake-detection", "one-sided", 32, 36, decisions),
    ]

    metrics = compute_metrics(
        selected,
        runs,
        {
            "usd": 0.001,
            "budgetUsd": 1.0,
            "calls": 16,
            "plannedSessions": 2,
            "aborted": False,
            "abortReason": None,
        },
    )

    check = next(
        item for item in metrics["discrimination"]["order"] if item["worseTier"] == "one-sided"
    )
    assert check["gap"] == -8.0
    assert check["ok"] is False
    assert metrics["discrimination"]["passed"] is False


def test_discrimination_is_not_measured_without_a_tier_pair() -> None:
    case = next(case for case in load_cases() if case.id == "strong-reusable-packaging")
    decisions = {investor_id: "in" for investor_id in INVESTOR_IDS}
    run = _raw_run(case.id, case.tier, 28, 28, decisions)
    metrics = compute_metrics(
        [case],
        [run],
        {"usd": 0.001, "plannedSessions": 1, "aborted": False, "abortReason": None},
    )

    assert metrics["discrimination"]["measured"] is False
    assert metrics["discrimination"]["passed"] is None
    report = render_report(
        cases=[case],
        metrics=metrics,
        expectations=[],
        issues=[],
        meta={},
    )
    assert "| Discrimination | not measured |" in report
    assert "Partial run cannot establish" in report


async def test_budget_cap_aborts_before_a_call_is_sent() -> None:
    cases = load_cases()
    meter = UsageMeter(budget_usd=0.001, price=PRICE)
    provider = FakeJuryProvider(cases, on_usage=meter.record)

    result = await run_benchmark(
        cases=cases,
        repeats=1,
        provider=provider,
        meter=meter,
        config=make_config(),
    )

    assert result["aborted"] is True
    assert result["spend"]["calls"] == 0
    assert provider.calls == 0
    assert result["runs"][0]["status"] == "budget-aborted"


def test_cli_requires_confirmation_and_makes_no_call(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--provider",
            "fake",
            "--repeats",
            "1",
            "--out-dir",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "model calls: 80 expected" in output
    assert "estimated:" in output
    assert "--confirm" in output
    assert not (tmp_path / "out").exists()


def test_cli_fake_run_writes_json_and_report(tmp_path, capsys) -> None:
    out_dir = tmp_path / "out"
    exit_code = main(
        [
            "--provider",
            "fake",
            "--repeats",
            "1",
            "--budget-usd",
            "1",
            "--out-dir",
            str(out_dir),
            "--confirm",
        ]
    )

    assert exit_code == 0
    runs = list(out_dir.glob("*/lion-benchmark.json"))
    reports = list(out_dir.glob("*/lion-benchmark.md"))
    assert len(runs) == 1
    assert len(reports) == 1

    payload = json.loads(runs[0].read_text(encoding="utf-8"))
    assert payload["meta"]["provider"] == "fake"
    assert payload["meta"]["repeats"] == 1
    assert payload["metrics"]["sessions"]["completed"] == 10
    assert len(payload["runs"]) == 10
    assert len(payload["expectations"]) > 0

    report = reports[0].read_text(encoding="utf-8")
    assert "Lion quality benchmark" in report
    assert "Cost per completed session" in report

    output = capsys.readouterr().out
    assert "Completed 10 of 10 sessions" in output


def test_cli_budget_abort_is_reported(tmp_path, capsys) -> None:
    out_dir = tmp_path / "out"
    exit_code = main(
        [
            "--provider",
            "fake",
            "--repeats",
            "1",
            "--budget-usd",
            "0.001",
            "--out-dir",
            str(out_dir),
            "--confirm",
        ]
    )

    assert exit_code == 1
    payload = json.loads(next(out_dir.glob("*/lion-benchmark.json")).read_text(encoding="utf-8"))
    assert payload["metrics"]["sessions"]["aborted"] is True
    assert payload["metrics"]["cost"]["calls"] == 0
    output = capsys.readouterr().out
    assert "aborted by the budget cap" in output


def test_cli_rejects_invalid_budgets(capsys) -> None:
    assert main(["--provider", "fake", "--budget-usd", "5"]) == 2
    assert "under 5 USD" in capsys.readouterr().err

    assert main(["--provider", "fake", "--budget-usd", "nan"]) == 2
    assert "finite and positive" in capsys.readouterr().err


def test_cli_rejects_an_unknown_case_id(tmp_path) -> None:
    exit_code = main(
        [
            "--provider",
            "fake",
            "--only",
            "does-not-exist",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert exit_code == 2


def test_cli_paid_run_needs_a_key_and_refuses_before_any_call(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr("backend.benchmark.cli.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEMO_MODE", "true")

    exit_code = main(["--repeats", "1", "--out-dir", str(tmp_path)])

    assert exit_code == 2
    assert "needs OPENAI_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_cli_subset_run_excludes_unevaluated_checks_from_pass_count(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--provider",
            "fake",
            "--repeats",
            "1",
            "--only",
            "strong-reusable-packaging",
            "--confirm",
            "--out-dir",
            str(tmp_path),
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    payload = json.loads(next(tmp_path.rglob("lion-benchmark.json")).read_text())
    unevaluated = [item for item in payload["expectations"] if item["ok"] is None]
    evaluated = [item for item in payload["expectations"] if item["ok"] is not None]
    assert len(unevaluated) == 1
    assert f"Expectations: {len(evaluated)} of {len(evaluated)} passed." in out
    report = next(tmp_path.rglob("lion-benchmark.md")).read_text()
    assert f"{len(evaluated)} of {len(evaluated)} checks passed." in report
