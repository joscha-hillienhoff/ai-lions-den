"""Command line entry point for the lion quality benchmark.

Run it with a real key:

    uv run python -m backend.benchmark --confirm

or verify the pipeline without any network call:

    uv run python -m backend.benchmark --provider fake --repeats 1 --confirm
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ..config import load_config
from ..jury.openai import OpenAiJuryProvider
from ..jury.pricing import ModelPrice, UnknownModelPrice, price_for
from ..jury.types import JuryProvider
from .cases import DEFAULT_CASES_PATH, CaseFileError, load_cases, select_cases
from .estimate import PlanEstimate, estimate_plan
from .fake import FakeJuryProvider
from .meter import UsageMeter
from .report import render_report
from .runner import run_benchmark
from .scoring import collect_issues, compute_metrics, evaluate_expectations

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = "data/benchmark"
DEFAULT_BUDGET_USD = 4.0
DEFAULT_REPEATS = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.benchmark",
        description="Run the lion quality benchmark against a fixed pitch set.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help=f"runs per defense scenario (default {DEFAULT_REPEATS})",
    )
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=DEFAULT_BUDGET_USD,
        help=f"hard spend cap below 5 US dollars (default {DEFAULT_BUDGET_USD})",
    )
    parser.add_argument(
        "--price-model",
        default=None,
        help="model key from backend/jury/pricing.py when OPENAI_MODEL is an Azure deployment name",
    )
    parser.add_argument("--cases", default=None, help="path to the benchmark case file")
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="CASE_ID",
        help="run only these case ids",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"gitignored output directory (default {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--provider",
        choices=("openai", "fake"),
        default="openai",
        help="openai for a real run, fake for local pipeline verification",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually start the run after the estimate is printed",
    )
    return parser


def _print_plan(
    *,
    cases_count: int,
    variants_count: int,
    repeats: int,
    estimate: PlanEstimate,
    budget: float,
    model: str,
    price: ModelPrice,
    cases_path: Path,
) -> None:
    print("Lion quality benchmark")
    print(
        f"  test set:    {cases_count} pitches, {variants_count} defense scenarios ({cases_path})"
    )
    print(f"  repeats:     {repeats}")
    print(f"  sessions:    {estimate.sessions}")
    print(f"  model calls: {estimate.calls} expected (4 round 1 + 4 verdict per session)")
    print(f"  model:       {model}")
    print(
        f"  price:       {price.input_per_million:.2f} in / "
        f"{price.output_per_million:.2f} out USD per 1M tokens"
    )
    print(
        f"  estimated:   about {estimate.prompt_tokens:,} prompt and "
        f"{estimate.completion_tokens:,} completion tokens, "
        f"{estimate.usd:.4f} USD"
    )
    print(f"  budget cap:  {budget:.2f} USD (a call is refused before the cap is crossed)")
    print()


def _print_progress(index: int, total: int, run: dict[str, Any]) -> None:
    round1 = run.get("round1") or {}
    verdict = run.get("verdict") or {}
    print(
        f"[{index:>2}/{total}] {run.get('caseId')}/{run.get('variantId')} "
        f"run {run.get('repeat')}: {run.get('status')} "
        f"round 1 {round1.get('total', '-')} -> final {verdict.get('total', '-')} "
        f"(in {verdict.get('inCount', '-')}/4) {run.get('durationMs', 0)}ms"
    )


def _run_dir(out_dir: Path) -> Path:
    base = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    candidate = out_dir / base
    suffix = 2
    while candidate.exists():
        candidate = out_dir / f"{base}-{suffix}"
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def _spend_payload(result: dict[str, Any], meter: UsageMeter) -> dict[str, Any]:
    return {
        **result["spend"],
        "budgetUsd": meter.budget_usd,
        "aborted": bool(result["aborted"]),
        "abortReason": result["abortReason"],
        "plannedSessions": result["plannedSessions"],
    }


def _write_outputs(
    *,
    run_dir: Path,
    payload: dict[str, Any],
    report: str,
) -> tuple[Path, Path]:
    json_path = run_dir / "lion-benchmark.json"
    report_path = run_dir / "lion-benchmark.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(report, encoding="utf-8")
    return json_path, report_path


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.repeats < 1:
        print("error: --repeats must be at least 1", file=sys.stderr)
        return 2
    if not math.isfinite(args.budget_usd) or args.budget_usd <= 0:
        print("error: --budget-usd must be finite and positive", file=sys.stderr)
        return 2
    if args.budget_usd >= 5:
        print("error: --budget-usd must be under 5 USD", file=sys.stderr)
        return 2

    # Match the app: normal OPENAI_* settings can live in .env.
    load_dotenv(ROOT / ".env")
    load_dotenv(ROOT / ".env.local", override=True)

    cases_path = Path(args.cases) if args.cases else DEFAULT_CASES_PATH
    try:
        cases = select_cases(load_cases(cases_path), args.only)
    except CaseFileError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    variants_count = sum(len(case.variants) for case in cases)
    if not cases or variants_count == 0:
        print("error: the selected test set is empty", file=sys.stderr)
        return 2

    config = load_config()
    if args.provider == "openai":
        if config.demo:
            print(
                "error: a paid run needs OPENAI_API_KEY and DEMO_MODE unset. "
                "Set the normal OPENAI_* variables, or use --provider fake to verify "
                "the pipeline without a network call.",
                file=sys.stderr,
            )
            return 2
        model = config.model
    else:
        model = "fake"

    price_model = args.price_model or (
        config.model if args.provider == "openai" else "gpt-4.1-mini"
    )
    try:
        price = price_for(price_model)
    except UnknownModelPrice as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    estimate = estimate_plan(cases, args.repeats, price)
    _print_plan(
        cases_count=len(cases),
        variants_count=variants_count,
        repeats=args.repeats,
        estimate=estimate,
        budget=args.budget_usd,
        model=model,
        price=price,
        cases_path=cases_path,
    )
    if not args.confirm:
        print("No calls were made. Re-run with --confirm to start.")
        return 2

    meter = UsageMeter(budget_usd=args.budget_usd, price=price)
    provider: JuryProvider
    if args.provider == "openai":
        provider = OpenAiJuryProvider(
            config,
            on_usage=meter.record,
            before_retry=meter.retry,
            on_preflight_failure=meter.release,
            benchmark_limits=True,
        )
    else:
        provider = FakeJuryProvider(cases, on_usage=meter.record)

    started = time.perf_counter()
    result = asyncio.run(
        run_benchmark(
            cases=cases,
            repeats=args.repeats,
            provider=provider,
            meter=meter,
            config=config,
            progress=_print_progress,
        )
    )
    elapsed_seconds = time.perf_counter() - started

    spend = _spend_payload(result, meter)
    metrics = compute_metrics(cases, result["runs"], spend)
    expectations = evaluate_expectations(cases, metrics, result["runs"])
    issues = collect_issues(cases, result["runs"], expectations)
    run_dir = _run_dir(Path(args.out_dir))
    meta = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": args.provider,
        "model": model,
        "priceModel": price_model,
        "inputPrice": price.input_per_million,
        "outputPrice": price.output_per_million,
        "repeats": args.repeats,
        "budgetUsd": args.budget_usd,
        "casesPath": str(cases_path),
        "elapsedSeconds": round(elapsed_seconds, 1),
        "runDir": str(run_dir),
    }
    report = render_report(
        cases=cases,
        metrics=metrics,
        expectations=expectations,
        issues=issues,
        meta=meta,
    )
    payload = {
        "meta": meta,
        "metrics": metrics,
        "expectations": expectations,
        "issues": issues,
        "runs": result["runs"],
    }
    json_path, report_path = _write_outputs(run_dir=run_dir, payload=payload, report=report)

    sessions = metrics["sessions"]
    passed = sum(1 for item in expectations if item["ok"] is True)
    evaluated = sum(1 for item in expectations if item["ok"] is not None)
    severities = {severity: 0 for severity in ("high", "medium", "low")}
    for issue in issues:
        severities[issue["severity"]] = severities.get(issue["severity"], 0) + 1

    print()
    print(
        f"Completed {sessions['completed']} of {sessions['planned']} sessions "
        f"in {elapsed_seconds:.1f}s."
    )
    print(
        f"Spend: {metrics['cost']['spendUsd']:.4f} USD of {args.budget_usd:.2f} USD "
        f"({metrics['cost']['calls']} calls, "
        f"{metrics['cost']['perSessionUsd'] or 0:.4f} USD per completed session)."
    )
    discrimination = metrics["discrimination"]
    discrimination_status = (
        "partial"
        if metrics["partial"]
        else "not measured"
        if not discrimination["measured"]
        else "partial"
        if not discrimination["complete"]
        else "pass"
        if discrimination["passed"]
        else "FAIL"
    )
    print(f"Discrimination: {discrimination_status}.")
    if metrics["partial"]:
        print("Expectations: partial (not evaluated).")
    else:
        print(f"Expectations: {passed} of {evaluated} passed.")
    print(
        f"Issues: {len(issues)} "
        f"({severities.get('high', 0)} high, {severities.get('medium', 0)} medium, "
        f"{severities.get('low', 0)} low)."
    )
    if result["aborted"]:
        print(f"Run aborted by the budget cap: {result['abortReason']}")
    print("Wrote:")
    print(f"  {json_path}")
    print(f"  {report_path}")

    if result["aborted"] or metrics["partial"] or sessions["completed"] == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
