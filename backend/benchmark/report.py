"""Markdown report for a lion quality benchmark run."""

from __future__ import annotations

from typing import Any

from .cases import LionCase


def _num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.0f}%"


def _yes_no(value: bool | None) -> str:
    if value is None:
        return "partial"
    return "pass" if value else "FAIL"


def _row(cells: list[Any]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [_row(headers), "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend(_row(row) for row in rows)
    return lines


def render_report(
    *,
    cases: list[LionCase],
    metrics: dict[str, Any],
    expectations: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    meta: dict[str, Any],
    title: str = "Lion quality benchmark",
    intro: str | None = None,
    run_rows: list[list[Any]] | None = None,
    preface: list[str] | None = None,
) -> str:
    """Render the Markdown report.

    The optional arguments let another runner that records the same run shape (the live
    benchmark) reuse every metric section with its own header: ``run_rows`` replaces the
    default run table, ``preface`` adds Markdown lines right after it.
    """
    sessions = metrics["sessions"]
    lines: list[str] = []

    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        intro
        or "A deterministic report over recorded jury output. No LLM judge is involved, "
        "so the metrics below are reproducible from the raw JSON."
    )
    lines.append("")
    if run_rows is None:
        cost = metrics["cost"]
        run_rows = [
            ["Generated", meta.get("generatedAt", "-")],
            ["Model", f"`{meta.get('model', '-')}`"],
            [
                "Price basis",
                f"`{meta.get('priceModel', '-')}` at "
                f"{_num(meta.get('inputPrice'), 2)} / "
                f"{_num(meta.get('outputPrice'), 2)} USD per 1M tokens",
            ],
            ["Repeats per scenario", meta.get("repeats", "-")],
            [
                "Test set",
                f"{len(cases)} pitches, "
                f"{sum(len(case.variants) for case in cases)} defense scenarios",
            ],
            [
                "Sessions",
                f"{sessions['completed']} completed of {sessions['planned']} planned "
                f"({sessions['failed']} failed or aborted)",
            ],
            [
                "Spend",
                f"{_num(cost['spendUsd'], 4)} USD of {_num(cost['budgetUsd'], 2)} USD budget",
            ],
            [
                "Cost per completed session",
                f"{_num(cost['perSessionUsd'], 4)} USD ({_num(cost['callsPerSession'], 2)} calls)",
            ],
        ]
    lines.extend(_table(["Run", "Value"], run_rows))
    lines.append("")
    if sessions.get("aborted"):
        lines.append(
            f"> Run aborted by the budget cap: {sessions.get('abortReason') or 'budget exhausted'}"
        )
        lines.append("")
    if preface:
        lines.extend(preface)
        lines.append("")

    passed = sum(1 for item in expectations if item["ok"] is True)
    evaluated = sum(1 for item in expectations if item["ok"] is not None)
    lines.append("## Headline")
    lines.append("")
    if metrics.get("partial"):
        lines.append(
            "> Partial benchmark: one or more requested sessions did not complete; "
            "quality metrics are not a pass."
        )
        lines.append("")
    lines.extend(
        _table(
            ["Metric", "Result", "Reading"],
            [
                [
                    "Discrimination",
                    _discrimination_result(
                        metrics["discrimination"], partial=bool(metrics.get("partial"))
                    ),
                    _discrimination_detail(
                        metrics["discrimination"], partial=bool(metrics.get("partial"))
                    ),
                ],
                [
                    "Defense effect",
                    _quality_result(metrics, _defense_reading(metrics["defense"])),
                    "A fixing answer should raise the score; a dodge should not",
                ],
                [
                    "Repeat consistency",
                    _quality_result(
                        metrics, f"score spread {_num(metrics['consistency']['scoreSpread'])}"
                    ),
                    _quality_result(
                        metrics,
                        f"in/out flip rate {_pct(metrics['consistency']['decisionFlipRate'])}",
                    ),
                ],
                [
                    "Lens adherence",
                    _quality_result(metrics, f"{_pct(metrics['lens']['ownLensRate'])} own-lens"),
                    _quality_result(
                        metrics, f"{_pct(metrics['lens']['leakRate'])} cross-lens leaks"
                    ),
                ],
                [
                    "Faithfulness",
                    _quality_result(metrics, _pct(metrics["faithfulness"]["supportedRate"])),
                    _quality_result(
                        metrics,
                        f"{len(metrics['faithfulness']['unsupported'])} unsupported of "
                        f"{metrics['faithfulness']['checks']} checks",
                    ),
                ],
                [
                    "I'm in rate",
                    _quality_result(metrics, _pct(metrics["isIn"]["rate"])),
                    "Share of the four investors who invest, over all verdicts",
                ],
                [
                    "Manipulation resistance",
                    _quality_result(metrics, _manipulation_reading(metrics.get("manipulation"))),
                    "The injected 10/10 case should stay low and not repeat the instruction",
                ],
                [
                    "Expectations",
                    "partial" if metrics.get("partial") else f"{passed} of {evaluated} passed",
                    "Machine-readable expectations from the case file",
                ],
            ],
        )
    )
    lines.append("")

    lines.append("## Scores by case")
    lines.append("")
    rows = []
    for row in metrics["byCase"]:
        rows.append(
            [
                f"`{row['caseId']}`",
                row["tier"],
                row["variantId"],
                row["sessions"],
                _num(row["meanRound1Total"]),
                _num(row["meanVerdictTotal"]),
                _num(row["meanDelta"]),
                _pct(row["inRate"]),
                _num(row["scoreSpread"]),
                _pct(row["decisionFlipRate"]),
            ]
        )
    lines.extend(
        _table(
            [
                "Case",
                "Tier",
                "Defense",
                "Runs",
                "Round 1",
                "Final",
                "Delta",
                "I'm in",
                "Spread",
                "Flips",
            ],
            rows,
        )
    )
    lines.append("")

    lines.append("## Discrimination by tier")
    lines.append("")
    tier_rows = [
        [tier, _num(mean), _pct(metrics["tierInRates"].get(tier))]
        for tier, mean in sorted(
            metrics["tierMeans"].items(),
            key=lambda item: -(item[1] if item[1] is not None else -1),
        )
    ]
    lines.extend(_table(["Tier", "Mean final total (of 40)", "I'm in rate"], tier_rows))
    lines.append("")
    lines.extend(
        _table(
            ["Should beat", "By at least", "Actual gap", "Result"],
            [
                [
                    check["betterTier"],
                    _num(check["minGap"], 1),
                    _num(check["gap"]),
                    _yes_no(check["ok"]),
                ]
                for check in metrics["discrimination"]["order"]
            ],
        )
    )
    lines.append("")

    lines.append("## Defense effect")
    lines.append("")
    defense_rows = []
    for kind in ("fix", "dodge", "neutral"):
        stats = metrics["defense"]["byVariantKind"].get(kind)
        if stats is None:
            continue
        defense_rows.append(
            [
                kind,
                stats["sessions"],
                _num(stats["meanDelta"]),
                _pct(stats["raiseRate"]),
                _pct(stats["holdRate"]),
                _pct(stats["dropRate"]),
            ]
        )
    lines.extend(
        _table(
            ["Answer kind", "Runs", "Mean delta", "Raised", "Held", "Dropped"],
            defense_rows,
        )
    )
    lines.append("")
    if metrics["defense"]["pairs"]:
        lines.extend(
            _table(
                ["Paired case", "Better", "Worse", "Better delta", "Worse delta", "Gap", "Result"],
                [
                    [
                        f"`{pair['caseId']}`",
                        pair["betterVariant"],
                        pair["worseVariant"],
                        _num(pair["betterDelta"]),
                        _num(pair["worseDelta"]),
                        _num(pair["gap"]),
                        _yes_no(pair["ok"]),
                    ]
                    for pair in metrics["defense"]["pairs"]
                ],
            )
        )
        lines.append("")

    lines.append("## Repeat consistency")
    lines.append("")
    consistency_rows = [
        [
            f"`{group['caseId']}`",
            group["variantId"],
            group["sessions"],
            _num(group["scoreSpread"]),
            _num(group["totalSpread"]),
            _pct(group["decisionFlipRate"]),
        ]
        for group in metrics["consistency"]["groups"]
    ]
    lines.extend(
        _table(
            ["Case", "Defense", "Runs", "Mean investor spread", "Total spread", "In/out flips"],
            consistency_rows,
        )
    )
    lines.append("")

    lines.append("## Lens adherence (keyword heuristic)")
    lines.append("")
    lens_rows = []
    for investor_id, stats in metrics["lens"]["byInvestor"].items():
        lens_rows.append(
            [
                investor_id,
                stats["texts"],
                _pct(stats["ownLensRate"]),
                _pct(stats["leakRate"]),
            ]
        )
    lines.extend(_table(["Investor", "Texts", "Own-lens terms", "Cross-lens leak"], lens_rows))
    lines.append("")
    lines.append(
        "This is a keyword proxy: a text passes when it contains at least one term from the "
        "investor's own lens, and leaks when it has no own-lens term and at least two foreign ones."
    )
    lines.append("")

    lines.append("## Faithfulness")
    lines.append("")
    lines.extend(
        _table(
            ["Check", "Value"],
            [
                ["Supported citations", _pct(metrics["faithfulness"]["supportedRate"])],
                ["Numbers and quotes checked", metrics["faithfulness"]["checks"]],
                ["Unsupported", len(metrics["faithfulness"]["unsupported"])],
                ["Deal constants allowed", "250,000 EUR and 10 percent equity"],
            ],
        )
    )
    lines.append(
        "This heuristic checks numbers and quoted phrases only; paraphrased invented facts "
        "require reading the worst examples below."
    )
    lines.append("")
    unsupported = metrics["faithfulness"]["unsupported"]
    if unsupported:
        lines.extend(
            _table(
                ["Case", "Phase", "Investor", "Type", "Claim"],
                [
                    [
                        f"`{item['caseId']}`",
                        item["phase"],
                        item["investor"],
                        item["type"],
                        item["value"],
                    ]
                    for item in unsupported[:15]
                ],
            )
        )
        lines.append("")
    else:
        lines.append("No unsupported number or quotation was found in the recorded text.")
        lines.append("")

    lines.append("## Length and tone")
    lines.append("")
    length = metrics["length"]
    lines.extend(
        _table(
            ["Text", "Count", "Mean words", "Max words", "Over target", "Other"],
            [
                [
                    "Assessment",
                    length["assessment"]["count"],
                    _num(length["assessment"]["meanWords"]),
                    length["assessment"]["maxWords"],
                    length["assessment"]["overTarget"],
                    f"{length['assessment']['underTarget']} under 15 words",
                ],
                [
                    "Question",
                    length["question"]["count"],
                    _num(length["question"]["meanWords"]),
                    length["question"]["maxWords"],
                    length["question"]["overTarget"],
                    f"{length['question']['notOneQuestion']} without exactly one ?",
                ],
                [
                    "Statement",
                    length["statement"]["count"],
                    _num(length["statement"]["meanWords"]),
                    length["statement"]["maxWords"],
                    length["statement"]["overTarget"],
                    f"{length['statement']['underTarget']} under 15 words",
                ],
            ],
        )
    )
    lines.append("")
    lines.extend(
        _table(
            ["Tone check", "Value"],
            [
                ["Statements address the team as you", _pct(metrics["tone"]["secondPersonRate"])],
                ["Meta or setup leakage", _pct(metrics["tone"]["metaLeakRate"])],
                ["Decision phrase matches the score", _pct(metrics["tone"]["decisionPhraseRate"])],
                ["Failed model calls", length["failedCalls"]],
            ],
        )
    )
    lines.append("")

    lines.append("## Expectations")
    lines.append("")
    failed = [item for item in expectations if item["ok"] is False]
    if metrics.get("partial"):
        lines.append("Partial benchmark: checks were not evaluated.")
    else:
        lines.append(f"{passed} of {evaluated} checks passed.")
    lines.append("")
    if failed:
        lines.extend(
            _table(
                ["Case", "Defense", "Check", "Expected", "Actual"],
                [
                    [
                        f"`{item['caseId']}`" if item["caseId"] else "all cases",
                        item["variantId"] or "-",
                        item["check"],
                        str(item["expected"]),
                        str(item["actual"]),
                    ]
                    for item in failed
                ],
            )
        )
        lines.append("")

    lines.append("## Worst examples")
    lines.append("")
    if issues:
        for issue in issues[:15]:
            location = issue.get("caseId") or "benchmark"
            if issue.get("variantId"):
                location = f"{location}/{issue['variantId']}"
            if issue.get("repeat") is not None:
                location = f"{location} run {issue['repeat']}"
            if issue.get("investor"):
                location = f"{location} {issue['investor']}"
            lines.append(
                f"- **{issue['severity']} {issue['kind']}** at `{location}`: {issue['detail']}"
            )
            if issue.get("quote"):
                lines.append(f"  > {issue['quote']}")
    else:
        lines.append("No issues were collected.")
    lines.append("")

    lines.append("## How to read this")
    lines.append("")
    lines.append(
        "- Score totals are the sum of the four final investor scores, so the range is 4 to 40. "
        "The 'I'm in' line is 7 per investor, or 28 total."
    )
    lines.append(
        "- Every run uses the real round 1, defense and verdict code path, including score "
        "clamping and the 7-point investment threshold."
    )
    lines.append(
        "- Lens adherence and faithfulness are deterministic proxies, not human judgment. "
        "They find clear violations and are deliberately conservative about wording."
    )
    lines.append(
        "- A single run is evidence about this prompt and model, not a guarantee about the "
        "next session. Compare reports from before and after a prompt change."
    )
    lines.append("")
    lines.append("## Next step")
    lines.append("")
    lines.append(
        "Review the worst examples and failed expectations for concrete prompt improvements, "
        "then apply the changes and validate them by re-running this same benchmark."
    )
    lines.append("")
    return "\n".join(lines)


def _quality_result(metrics: dict[str, Any], value: str) -> str:
    return f"partial: {value}" if metrics.get("partial") else value


def _discrimination_result(discrimination: dict[str, Any], *, partial: bool = False) -> str:
    if partial:
        return "partial"
    if not discrimination.get("measured"):
        return "not measured"
    if not discrimination.get("complete"):
        return "partial"
    return _yes_no(discrimination["passed"])


def _discrimination_detail(discrimination: dict[str, Any], *, partial: bool = False) -> str:
    if partial:
        return "One or more requested sessions did not complete"
    if not discrimination.get("measured"):
        return "Partial run cannot establish the discrimination criterion"
    if not discrimination.get("complete"):
        present = ", ".join(discrimination.get("presentTiers", [])) or "none"
        missing = ", ".join(discrimination.get("missingTiers", [])) or "none"
        return f"Tiers run: {present}; missing: {missing}"
    return "Strong pitches rank above mixed, one-sided, weak, troll and manipulation"


def _defense_reading(defense: dict[str, Any]) -> str:
    fix = defense["byVariantKind"].get("fix")
    dodge = defense["byVariantKind"].get("dodge")
    if fix is None or dodge is None:
        return "not measured"
    return f"fix {_num(fix['meanDelta'])} vs dodge {_num(dodge['meanDelta'])}"


def _manipulation_reading(manipulation: dict[str, Any] | None) -> str:
    if manipulation is None:
        return "not measured"
    return (
        f"mean {_num(manipulation['meanTotal'])} of 40, "
        f"in-rate {_pct(manipulation['inRate'])}, "
        f"resisted {_yes_no(manipulation['resisted'])}, "
        f"{len(manipulation['forbiddenHits'])} forbidden hits"
    )
