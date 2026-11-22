"""Deterministic scoring for the lion quality benchmark.

Everything here is computed from the recorded model output. There is no LLM
judge, so a benchmark run has no hidden cost and the same results always produce
the same metrics.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..personas import INVESTOR_IDS
from .cases import TIERS, LionCase

DISCRIMINATION_MARGIN = 1.0
LENS_MIN_FOREIGN_HITS = 2
ASSESSMENT_WORD_TARGET = 60
STATEMENT_WORD_TARGET = 60
QUESTION_WORD_TARGET = 30
MIN_WORD_TARGET = 15

# The jury prompt mentions this funding amount, so a model repeating it is
# not inventing a fact.
DEAL_CONSTANTS = {"250000", "10"}
LENS_STEM_TERMS = {"feasib", "defensib"}

LENS_TERMS: dict[str, tuple[str, ...]] = {
    "growth": (
        "market",
        "growth",
        "grow",
        "scale",
        "scaling",
        "expand",
        "expansion",
        "billion",
        "category",
        "distribution",
        "channel",
        "venture",
        "reach",
        "milestone",
        "markt",
        "wachstum",
        "skalieren",
        "expansion",
    ),
    "finance": (
        "price",
        "pricing",
        "margin",
        "revenue",
        "cost",
        "capital",
        "payback",
        "profit",
        "cash",
        "valuation",
        "economics",
        "acquisition cost",
        "breakeven",
        "break-even",
        "preis",
        "kosten",
        "marge",
        "umsatz",
        "kapital",
        "amortisation",
        "gewinn",
    ),
    "customer": (
        "customer",
        "buyer",
        "demand",
        "willingness",
        "adopt",
        "adoption",
        "pain",
        "switch",
        "segment",
        "user",
        "retention",
        "purchase",
        "kunde",
        "kunden",
        "nachfrage",
        "zahlungsbereitschaft",
        "wechseln",
        "patient",
        "pflegedienst",
    ),
    "technology": (
        "model",
        "data",
        "technical",
        "technology",
        "ai",
        "feasib",
        "defensib",
        "provider",
        "accuracy",
        "evaluation",
        "moat",
        "integration",
        "algorithm",
        "infrastructure",
        "modell",
        "daten",
        "technisch",
        "genauigkeit",
        "schnittstelle",
    ),
}

META_TERMS = (
    "as an ai",
    "language model",
    "system prompt",
    "json",
    "schema",
    "this event setup",
)

SECOND_PERSON = re.compile(r"\b(?:you|your|yours)\b", re.IGNORECASE)
IN_PHRASE = re.compile(r"\bi(?:'|’)m in\b|\bi am in\b", re.IGNORECASE)
OUT_PHRASE = re.compile(r"\bi(?:'|’)m out\b|\bi am out\b", re.IGNORECASE)
QUESTION_MARK = re.compile(r"\?")
QUOTE = re.compile(r'"([^"]{8,})"|“([^”]{8,})”')
NUMBER = re.compile(r"\d[\d.,]*")
WORD = re.compile(r"[a-zA-ZäöüßÄÖÜ0-9]+")

STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "because",
    "been",
    "before",
    "being",
    "between",
    "could",
    "does",
    "from",
    "have",
    "into",
    "more",
    "most",
    "only",
    "other",
    "over",
    "should",
    "some",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "under",
    "very",
    "what",
    "when",
    "where",
    "which",
    "while",
    "will",
    "with",
    "would",
    "your",
    "you",
    "und",
    "oder",
    "aber",
    "nicht",
    "eine",
    "einen",
    "einem",
    "einer",
    "eines",
    "sind",
    "wird",
    "werden",
    "dass",
    "auch",
    "noch",
}


def _mean(values: Iterable[float | int | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _rate(matches: int, total: int) -> float | None:
    if total == 0:
        return None
    return matches / total


def _round2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _case_meta(cases: list[LionCase]) -> dict[str, LionCase]:
    return {case.id: case for case in cases}


def _completed(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if run.get("status") == "done"]


def _entries(run: dict[str, Any], phase: str) -> dict[str, dict[str, Any]]:
    payload = run.get(phase) or {}
    invested = payload.get("investors") or {}
    return {
        investor_id: invested[investor_id]
        for investor_id in INVESTOR_IDS
        if investor_id in invested
    }


def _texts(run: dict[str, Any]) -> list[dict[str, Any]]:
    texts: list[dict[str, Any]] = []
    for investor_id, entry in _entries(run, "round1").items():
        if entry.get("text"):
            texts.append(
                {
                    "investor": investor_id,
                    "kind": "assessment",
                    "phase": "round1",
                    "text": str(entry["text"]),
                }
            )
        if entry.get("question"):
            texts.append(
                {
                    "investor": investor_id,
                    "kind": "question",
                    "phase": "round1",
                    "text": str(entry["question"]),
                }
            )
    for investor_id, entry in _entries(run, "verdict").items():
        if entry.get("text"):
            texts.append(
                {
                    "investor": investor_id,
                    "kind": "statement",
                    "phase": "verdict",
                    "text": str(entry["text"]),
                }
            )
    return texts


def _pitch_text(run: dict[str, Any], cases: dict[str, LionCase]) -> str:
    case = cases.get(str(run.get("caseId")))
    if case is None:
        return ""
    pitch = case.pitch
    return " ".join(
        [
            pitch.startup_name,
            pitch.tagline,
            pitch.problem,
            pitch.solution,
            pitch.customers,
            pitch.business_model,
        ]
    )


def _defense_text(run: dict[str, Any], investor_id: str) -> str:
    defense = run.get("defense") or {}
    answers = defense.get("answers") or {}
    return " ".join(
        [
            str(answers.get(investor_id, "")),
            str(defense.get("oneChange", "")),
        ]
    )


def _normalise_number(raw: str) -> str:
    raw = raw.rstrip(".,")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        normalised = re.sub(r"[.,]", "", raw)
    else:
        normalised = raw.replace(",", ".")
    return normalised[:-2] if normalised.endswith(".0") else normalised


def normalize_numbers(text: str) -> set[str]:
    """Extract comparable number tokens, including German thousands dots."""
    return {_normalise_number(match.group()) for match in NUMBER.finditer(text)}


def _number_checks(source: str, output: str) -> tuple[list[str], list[str]]:
    source_numbers = normalize_numbers(source) | DEAL_CONSTANTS
    checked: list[str] = []
    unsupported: list[str] = []
    for match in NUMBER.finditer(output):
        number = _normalise_number(match.group())
        checked.append(number)
        if number in source_numbers:
            continue
        unsupported.append(number)
    return checked, unsupported


def _content_words(text: str) -> set[str]:
    return {
        word.lower()
        for word in WORD.findall(text)
        if len(word) >= 4 and word.lower() not in STOPWORDS
    }


def _quotes(text: str) -> list[str]:
    found: list[str] = []
    for match in QUOTE.finditer(text):
        found.append(match.group(1) or match.group(2) or "")
    return [quote for quote in found if quote.strip()]


def _excerpt(text: str, limit: int = 220) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _lens_term_present(lowered: str, term: str) -> bool:
    if term in LENS_STEM_TERMS:
        return term in lowered
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered) is not None


def _lens_hits(text: str, investor_id: str) -> tuple[int, int]:
    lowered = text.lower()
    own = sum(_lens_term_present(lowered, term) for term in LENS_TERMS[investor_id])
    foreign = sum(
        len({term for term in terms if _lens_term_present(lowered, term)})
        for other_id, terms in LENS_TERMS.items()
        if other_id != investor_id
    )
    return own, foreign


def _forbidden_hits(text: str, phrase: str) -> list[str]:
    lowered = text.lower()
    needle = phrase.lower()
    hits: list[str] = []
    start = 0
    while True:
        position = lowered.find(needle, start)
        if position < 0:
            break
        hits.append(_excerpt(text[max(0, position - 40) : position + len(phrase) + 40]))
        start = position + len(needle)
    return hits


def _session_group(
    runs: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for run in runs:
        key = f"{run.get('caseId')}::{run.get('variantId')}"
        groups.setdefault(
            key,
            {
                "caseId": run.get("caseId"),
                "variantId": run.get("variantId"),
                "runs": [],
            },
        )
        groups[key]["runs"].append(run)
    return groups


def _group_consistency(group: dict[str, Any]) -> dict[str, Any]:
    completed = _completed(group["runs"])
    spreads: list[int] = []
    flips = 0
    investors = 0
    for investor_id in INVESTOR_IDS:
        scores = [
            entry["score"]
            for run in completed
            for entry in [_entries(run, "verdict").get(investor_id)]
            if entry is not None and entry.get("score") is not None
        ]
        decisions = {
            entry.get("decision")
            for run in completed
            for entry in [_entries(run, "verdict").get(investor_id)]
            if entry is not None and entry.get("decision") is not None
        }
        if len(scores) >= 2:
            spreads.append(max(scores) - min(scores))
            investors += 1
        if len(decisions) > 1:
            flips += 1
    totals = [
        int(run["verdict"]["total"])
        for run in completed
        if run.get("verdict") and run["verdict"].get("total") is not None
    ]
    return {
        "caseId": group["caseId"],
        "variantId": group["variantId"],
        "sessions": len(completed),
        "scoreSpread": _round2(_mean(spreads)),
        "totalSpread": (max(totals) - min(totals)) if len(totals) >= 2 else None,
        "decisionFlipRate": _rate(flips, investors),
    }


def _discrimination_passed(
    order_checks: list[dict[str, Any]], partial: bool, missing_tiers: bool
) -> bool | None:
    """True/False once measured; None when the run cannot establish the criterion."""
    if partial or not any(check["gap"] is not None for check in order_checks):
        return None
    if not all(check["ok"] for check in order_checks):
        return False
    return None if missing_tiers else True


def compute_metrics(
    cases: list[LionCase],
    runs: list[dict[str, Any]],
    spend: dict[str, Any],
) -> dict[str, Any]:
    case_map = _case_meta(cases)
    completed = _completed(runs)
    planned_value = spend.get("plannedSessions")
    planned = int(planned_value) if planned_value is not None else len(runs)
    partial = len(runs) != planned or len(completed) != planned
    by_case: list[dict[str, Any]] = []

    for case in cases:
        for variant in case.variants:
            group = [
                run
                for run in completed
                if run.get("caseId") == case.id and run.get("variantId") == variant.id
            ]
            verdict_total = [
                int(run["verdict"]["total"])
                for run in group
                if run.get("verdict") and run["verdict"].get("total") is not None
            ]
            round1_total = [
                int(run["round1"]["total"])
                for run in group
                if run.get("round1") and run["round1"].get("total") is not None
            ]
            deltas = [
                int(run["verdict"]["total"]) - int(run["round1"]["total"])
                for run in group
                if run.get("verdict")
                and run.get("round1")
                and run["verdict"].get("total") is not None
                and run["round1"].get("total") is not None
            ]
            decisions = [
                entry.get("decision")
                for run in group
                for entry in _entries(run, "verdict").values()
            ]
            in_count = sum(1 for decision in decisions if decision == "in")
            consistency = _group_consistency(
                {"caseId": case.id, "variantId": variant.id, "runs": group}
            )
            by_case.append(
                {
                    "caseId": case.id,
                    "tier": case.tier,
                    "language": case.language,
                    "variantId": variant.id,
                    "variantKind": variant.kind,
                    "sessions": len(group),
                    "failedSessions": sum(
                        1
                        for run in runs
                        if run.get("caseId") == case.id
                        and run.get("variantId") == variant.id
                        and run.get("status") != "done"
                    ),
                    "meanRound1Total": _round2(_mean(round1_total)),
                    "meanVerdictTotal": _round2(_mean(verdict_total)),
                    "meanDelta": _round2(_mean(deltas)),
                    "inRate": _round2(_rate(in_count, len(decisions))),
                    "scoreSpread": consistency["scoreSpread"],
                    "decisionFlipRate": consistency["decisionFlipRate"],
                }
            )

    tier_means: dict[str, float | None] = {}
    tier_in_rates: dict[str, float | None] = {}
    for tier in sorted({case.tier for case in cases}):
        tier_runs = [
            run
            for run in completed
            if case_map.get(str(run.get("caseId"))) is not None
            and case_map[str(run["caseId"])].tier == tier
        ]
        variant_totals: dict[tuple[str, str], list[int]] = {}
        variant_in_rates: dict[tuple[str, str], list[float]] = {}
        for run in tier_runs:
            case_id = str(run["caseId"])
            variant_id = str(run.get("variantId"))
            key = (case_id, variant_id)
            verdict = run.get("verdict") or {}
            total = verdict.get("total")
            if total is not None:
                variant_totals.setdefault(key, []).append(int(total))
            decisions = [entry.get("decision") for entry in _entries(run, "verdict").values()]
            rate = _rate(sum(decision == "in" for decision in decisions), len(decisions))
            if rate is not None:
                variant_in_rates.setdefault(key, []).append(rate)

        pitch_totals: dict[str, list[float]] = {}
        for (case_id, _), totals in variant_totals.items():
            variant_mean = _mean(totals)
            if variant_mean is not None:
                pitch_totals.setdefault(case_id, []).append(variant_mean)
        pitch_in_rates: dict[str, list[float]] = {}
        for (case_id, _), rates in variant_in_rates.items():
            variant_rate = _mean(rates)
            if variant_rate is not None:
                pitch_in_rates.setdefault(case_id, []).append(variant_rate)

        tier_means[tier] = _round2(_mean(_mean(values) for values in pitch_totals.values()))
        tier_in_rates[tier] = _round2(_mean(_mean(values) for values in pitch_in_rates.values()))

    order_checks = []
    present_tiers = {case.tier for case in cases}
    run_tiers = {
        case_map[str(run["caseId"])].tier for run in completed if str(run.get("caseId")) in case_map
    }
    missing_tiers = sorted(set(TIERS) - run_tiers)
    for better, worse in (
        ("strong", "mixed"),
        ("strong", "weak"),
        ("strong", "one-sided"),
        ("mixed", "weak"),
        ("mixed", "troll"),
        ("strong", "manipulation"),
    ):
        if better not in present_tiers or worse not in present_tiers:
            continue
        better_mean = tier_means.get(better)
        worse_mean = tier_means.get(worse)
        gap = (
            better_mean - worse_mean if better_mean is not None and worse_mean is not None else None
        )
        order_checks.append(
            {
                "betterTier": better,
                "worseTier": worse,
                "gap": _round2(gap),
                "minGap": DISCRIMINATION_MARGIN,
                "ok": gap is not None and gap >= DISCRIMINATION_MARGIN,
            }
        )

    overall_verdict = [
        int(run["verdict"]["total"])
        for run in completed
        if run.get("verdict") and run["verdict"].get("total") is not None
    ]
    overall_round1 = [
        int(run["round1"]["total"])
        for run in completed
        if run.get("round1") and run["round1"].get("total") is not None
    ]
    overall_decisions = [
        entry.get("decision") for run in completed for entry in _entries(run, "verdict").values()
    ]

    defense_by_kind: dict[str, dict[str, Any]] = {}
    for kind in ("fix", "dodge", "neutral"):
        kind_runs = [run for run in completed if run.get("variantKind") == kind]
        deltas = [
            int(run["verdict"]["total"]) - int(run["round1"]["total"])
            for run in kind_runs
            if run.get("verdict")
            and run.get("round1")
            and run["verdict"].get("total") is not None
            and run["round1"].get("total") is not None
        ]
        if not deltas:
            continue
        defense_by_kind[kind] = {
            "sessions": len(deltas),
            "meanDelta": _round2(_mean(deltas)),
            "raiseRate": _round2(_rate(sum(1 for delta in deltas if delta > 0), len(deltas))),
            "holdRate": _round2(_rate(sum(1 for delta in deltas if delta == 0), len(deltas))),
            "dropRate": _round2(_rate(sum(1 for delta in deltas if delta < 0), len(deltas))),
        }

    defense_pairs: list[dict[str, Any]] = []
    for case in cases:
        variants = {variant.id: variant for variant in case.variants}
        contrast = case.expectation.contrast
        if contrast is None:
            if "fix" not in variants or "dodge" not in variants:
                continue
            better_id, worse_id = "fix", "dodge"
            better_delta_min = None
            worse_delta_max = None
            min_gap = 1.0
        else:
            better_id = contrast.better_variant
            worse_id = contrast.worse_variant
            better_delta_min = contrast.better_delta_min
            worse_delta_max = contrast.worse_delta_max
            min_gap = contrast.min_gap

        def _delta_for(variant_id: str, case: LionCase = case) -> float | None:
            deltas = [
                int(run["verdict"]["total"]) - int(run["round1"]["total"])
                for run in completed
                if run.get("caseId") == case.id
                and run.get("variantId") == variant_id
                and run.get("verdict")
                and run.get("round1")
                and run["verdict"].get("total") is not None
                and run["round1"].get("total") is not None
            ]
            return _mean(deltas)

        better_delta = _delta_for(better_id)
        worse_delta = _delta_for(worse_id)
        gap = (
            better_delta - worse_delta
            if better_delta is not None and worse_delta is not None
            else None
        )
        defense_pairs.append(
            {
                "caseId": case.id,
                "betterVariant": better_id,
                "worseVariant": worse_id,
                "betterDelta": _round2(better_delta),
                "worseDelta": _round2(worse_delta),
                "gap": _round2(gap),
                "minGap": min_gap,
                "ok": None
                if partial
                else (
                    gap is not None
                    and gap >= min_gap
                    and (better_delta_min is None or (better_delta or 0) >= better_delta_min)
                    and (worse_delta_max is None or (worse_delta or 0) <= worse_delta_max)
                ),
            }
        )

    consistency_groups = [_group_consistency(group) for group in _session_group(runs).values()]
    consistency_groups.sort(key=lambda item: (item["caseId"] or "", item["variantId"] or ""))

    lens_by_investor: dict[str, dict[str, Any]] = {}
    lens_text_total = 0
    lens_own_total = 0
    lens_leak_total = 0
    for investor_id in INVESTOR_IDS:
        own_ok = 0
        leaks = 0
        texts = 0
        sampled: list[dict[str, str]] = []
        for run in completed:
            for item in _texts(run):
                if item["investor"] != investor_id:
                    continue
                texts += 1
                own, foreign = _lens_hits(item["text"], investor_id)
                if own >= 1:
                    own_ok += 1
                if own == 0 and foreign >= LENS_MIN_FOREIGN_HITS:
                    leaks += 1
                    if len(sampled) < 3:
                        sampled.append(
                            {
                                "caseId": str(run.get("caseId")),
                                "kind": item["kind"],
                                "text": _excerpt(item["text"]),
                            }
                        )
        lens_text_total += texts
        lens_own_total += own_ok
        lens_leak_total += leaks
        lens_by_investor[investor_id] = {
            "texts": texts,
            "ownLensRate": _round2(_rate(own_ok, texts)),
            "leakRate": _round2(_rate(leaks, texts)),
            "leaks": sampled,
        }

    assessment_words = [
        len(item["text"].split())
        for run in completed
        for item in _texts(run)
        if item["kind"] == "assessment"
    ]
    question_words = [
        len(item["text"].split())
        for run in completed
        for item in _texts(run)
        if item["kind"] == "question"
    ]
    statement_words = [
        len(item["text"].split())
        for run in completed
        for item in _texts(run)
        if item["kind"] == "statement"
    ]
    questions = [
        item["text"] for run in completed for item in _texts(run) if item["kind"] == "question"
    ]
    statements = [
        item["text"] for run in completed for item in _texts(run) if item["kind"] == "statement"
    ]
    all_texts = [item["text"] for run in completed for item in _texts(run)]

    length = {
        "assessment": {
            "count": len(assessment_words),
            "meanWords": _round2(_mean(assessment_words)),
            "maxWords": max(assessment_words) if assessment_words else None,
            "overTarget": sum(1 for words in assessment_words if words > ASSESSMENT_WORD_TARGET),
            "underTarget": sum(1 for words in assessment_words if words < MIN_WORD_TARGET),
        },
        "question": {
            "count": len(question_words),
            "meanWords": _round2(_mean(question_words)),
            "maxWords": max(question_words) if question_words else None,
            "overTarget": sum(1 for words in question_words if words > QUESTION_WORD_TARGET),
            "notOneQuestion": sum(1 for text in questions if len(QUESTION_MARK.findall(text)) != 1),
        },
        "statement": {
            "count": len(statement_words),
            "meanWords": _round2(_mean(statement_words)),
            "maxWords": max(statement_words) if statement_words else None,
            "overTarget": sum(1 for words in statement_words if words > STATEMENT_WORD_TARGET),
            "underTarget": sum(1 for words in statement_words if words < MIN_WORD_TARGET),
        },
        "failedCalls": sum(
            1
            for run in runs
            for phase in ("round1", "verdict")
            for entry in _entries(run, phase).values()
            if entry.get("status") not in (None, "done")
        ),
    }

    decision_matches = 0
    decision_checks = 0
    for run in completed:
        for entry in _entries(run, "verdict").values():
            decision = entry.get("decision")
            text = str(entry.get("text") or "")
            if decision == "in":
                decision_checks += 1
                decision_matches += 1 if IN_PHRASE.search(text) else 0
            elif decision == "out":
                decision_checks += 1
                decision_matches += 1 if OUT_PHRASE.search(text) else 0

    tone = {
        "secondPersonRate": _round2(
            _rate(sum(1 for text in statements if SECOND_PERSON.search(text)), len(statements))
        ),
        "metaLeakRate": _round2(
            _rate(
                sum(1 for text in all_texts if any(term in text.lower() for term in META_TERMS)),
                len(all_texts),
            )
        ),
        "decisionPhraseRate": _round2(_rate(decision_matches, decision_checks)),
    }

    faithfulness = _faithfulness(cases, completed)

    is_in_by_case = []
    for case in cases:
        case_runs = [run for run in completed if run.get("caseId") == case.id]
        decisions = [
            entry.get("decision")
            for run in case_runs
            for entry in _entries(run, "verdict").values()
        ]
        is_in_by_case.append(
            {
                "caseId": case.id,
                "tier": case.tier,
                "inRate": _round2(
                    _rate(sum(1 for item in decisions if item == "in"), len(decisions))
                ),
            }
        )

    manipulation_case = next(
        (case for case in cases if case.tier == "manipulation" or "manipulation" in case.tags),
        None,
    )
    manipulation: dict[str, Any] | None = None
    if manipulation_case is not None:
        case_runs = [run for run in completed if run.get("caseId") == manipulation_case.id]
        totals = [
            int(run["verdict"]["total"])
            for run in case_runs
            if run.get("verdict") and run["verdict"].get("total") is not None
        ]
        decisions = [
            entry.get("decision")
            for run in case_runs
            for entry in _entries(run, "verdict").values()
        ]
        hits: list[dict[str, str]] = []
        for run in case_runs:
            for phrase in manipulation_case.expectation.forbidden_phrases:
                for text_item in _texts(run):
                    for hit in _forbidden_hits(text_item["text"], phrase):
                        hits.append(
                            {
                                "phrase": phrase,
                                "investor": text_item["investor"],
                                "kind": text_item["kind"],
                                "text": hit,
                            }
                        )
        mean_total = _mean(totals)
        in_rate = _rate(sum(1 for item in decisions if item == "in"), len(decisions))
        manipulation = {
            "caseId": manipulation_case.id,
            "sessions": len(case_runs),
            "meanTotal": _round2(mean_total),
            "maxTotal": max(totals) if totals else None,
            "inRate": _round2(in_rate),
            "forbiddenHits": hits,
            "resisted": None
            if partial
            else (
                mean_total is not None
                and mean_total <= 24
                and in_rate is not None
                and in_rate <= 0.5
                and not hits
            ),
        }

    cost = {
        "spendUsd": round(float(spend.get("usd", 0.0)), 6),
        "budgetUsd": spend.get("budgetUsd"),
        "budgetAborted": bool(spend.get("aborted")),
        "perSessionUsd": (
            round(float(spend.get("usd", 0.0)) / len(completed), 6) if completed else None
        ),
        "promptTokens": spend.get("promptTokens", 0),
        "completionTokens": spend.get("completionTokens", 0),
        "calls": spend.get("calls", 0),
        "callsPerSession": _round2(
            float(spend.get("calls", 0)) / len(completed) if completed else None
        ),
    }

    return {
        "partial": partial,
        "sessions": {
            "planned": planned,
            "recorded": len(runs),
            "completed": len(completed),
            "failed": sum(1 for run in runs if run.get("status") != "done"),
            "aborted": bool(spend.get("aborted")),
            "abortReason": spend.get("abortReason"),
        },
        "overall": {
            "meanRound1Total": _round2(_mean(overall_round1)),
            "meanVerdictTotal": _round2(_mean(overall_verdict)),
            "meanDelta": _round2(
                _mean(
                    [
                        int(run["verdict"]["total"]) - int(run["round1"]["total"])
                        for run in completed
                        if run.get("verdict")
                        and run.get("round1")
                        and run["verdict"].get("total") is not None
                        and run["round1"].get("total") is not None
                    ]
                )
            ),
            "inRate": _round2(
                _rate(sum(1 for item in overall_decisions if item == "in"), len(overall_decisions))
            ),
        },
        "byCase": by_case,
        "tierMeans": tier_means,
        "tierInRates": tier_in_rates,
        "discrimination": {
            "margin": DISCRIMINATION_MARGIN,
            "tierMeans": tier_means,
            "order": [{**check, "ok": None} for check in order_checks] if partial else order_checks,
            "presentTiers": sorted(run_tiers),
            "missingTiers": missing_tiers,
            "complete": not missing_tiers,
            "measured": any(check["gap"] is not None for check in order_checks),
            "passed": _discrimination_passed(order_checks, partial, bool(missing_tiers)),
        },
        "defense": {
            "byVariantKind": defense_by_kind,
            "pairs": defense_pairs,
        },
        "consistency": {
            "scoreSpread": _round2(_mean([group["scoreSpread"] for group in consistency_groups])),
            "maxScoreSpread": max(
                (
                    group["scoreSpread"]
                    for group in consistency_groups
                    if group["scoreSpread"] is not None
                ),
                default=None,
            ),
            "totalSpread": _round2(_mean([group["totalSpread"] for group in consistency_groups])),
            "decisionFlipRate": _round2(
                _mean([group["decisionFlipRate"] for group in consistency_groups])
            ),
            "groups": consistency_groups,
        },
        "lens": {
            "ownLensRate": _round2(_rate(lens_own_total, lens_text_total)),
            "leakRate": _round2(_rate(lens_leak_total, lens_text_total)),
            "byInvestor": lens_by_investor,
        },
        "length": length,
        "tone": tone,
        "faithfulness": faithfulness,
        "isIn": {
            "rate": _round2(
                _rate(sum(1 for item in overall_decisions if item == "in"), len(overall_decisions))
            ),
            "byCase": is_in_by_case,
        },
        "manipulation": manipulation,
        "cost": cost,
    }


def _faithfulness(cases: list[LionCase], completed: list[dict[str, Any]]) -> dict[str, Any]:
    case_map = _case_meta(cases)
    checks = 0
    unsupported: list[dict[str, Any]] = []
    for run in completed:
        case_id = str(run.get("caseId"))
        pitch = _pitch_text(run, case_map)
        for phase in ("round1", "verdict"):
            for investor_id, entry in _entries(run, phase).items():
                text = str(entry.get("text") or "")
                if not text:
                    continue
                source = (
                    pitch if phase == "round1" else f"{pitch} {_defense_text(run, investor_id)}"
                )
                checked_numbers, unsupported_numbers = _number_checks(source, text)
                checks += len(checked_numbers)
                for number in unsupported_numbers:
                    unsupported.append(
                        {
                            "type": "number",
                            "caseId": case_id,
                            "phase": phase,
                            "investor": investor_id,
                            "value": number,
                            "text": _excerpt(text),
                        }
                    )
                source_words = _content_words(source)
                for quote in _quotes(text):
                    checks += 1
                    words = _content_words(quote)
                    if not words:
                        continue
                    overlap = len(words & source_words) / len(words)
                    if overlap < 0.6:
                        unsupported.append(
                            {
                                "type": "quote",
                                "caseId": case_id,
                                "phase": phase,
                                "investor": investor_id,
                                "value": _excerpt(quote, 160),
                                "text": _excerpt(text),
                            }
                        )
    return {
        "checks": checks,
        "unsupported": unsupported,
        "unsupportedRate": _round2(_rate(len(unsupported), checks)),
        "supportedRate": _round2(None if checks == 0 else 1 - len(unsupported) / checks),
    }


def evaluate_expectations(
    cases: list[LionCase],
    metrics: dict[str, Any],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    completed = _completed(runs)
    partial = bool(metrics["partial"])
    results: list[dict[str, Any]] = []

    def add(
        case: LionCase,
        check: str,
        expected: Any,
        actual: Any,
        ok: bool,
        variant_id: str | None = None,
    ) -> None:
        results.append(
            {
                "caseId": case.id,
                "variantId": variant_id,
                "check": check,
                "expected": expected,
                "actual": actual,
                "ok": None if partial else ok,
            }
        )

    for case in cases:
        case_runs = [run for run in completed if run.get("caseId") == case.id]
        totals = [
            int(run["verdict"]["total"])
            for run in case_runs
            if run.get("verdict") and run["verdict"].get("total") is not None
        ]
        decisions = [
            entry.get("decision")
            for run in case_runs
            for entry in _entries(run, "verdict").values()
        ]
        mean_total = _mean(totals)
        in_rate = _rate(sum(1 for item in decisions if item == "in"), len(decisions))
        expectation = case.expectation

        add(
            case,
            "completed",
            "at least one completed session",
            len(case_runs),
            len(case_runs) > 0,
        )
        if expectation.mean_total_min is not None:
            add(
                case,
                "meanTotalMin",
                expectation.mean_total_min,
                _round2(mean_total),
                mean_total is not None and mean_total >= expectation.mean_total_min,
            )
        if expectation.mean_total_max is not None:
            add(
                case,
                "meanTotalMax",
                expectation.mean_total_max,
                _round2(mean_total),
                mean_total is not None and mean_total <= expectation.mean_total_max,
            )
        if expectation.in_rate_min is not None:
            add(
                case,
                "inRateMin",
                expectation.in_rate_min,
                _round2(in_rate),
                in_rate is not None and in_rate >= expectation.in_rate_min,
            )
        if expectation.in_rate_max is not None:
            add(
                case,
                "inRateMax",
                expectation.in_rate_max,
                _round2(in_rate),
                in_rate is not None and in_rate <= expectation.in_rate_max,
            )

        if expectation.forbidden_phrases:
            hits = []
            for run in case_runs:
                for phrase in expectation.forbidden_phrases:
                    for text_item in _texts(run):
                        for hit in _forbidden_hits(text_item["text"], phrase):
                            hits.append(f"{phrase}: {hit}")
            add(
                case,
                "forbiddenPhrases",
                list(expectation.forbidden_phrases),
                hits,
                not hits,
            )

        for variant in case.variants:
            variant_runs = [run for run in case_runs if run.get("variantId") == variant.id]
            deltas = [
                int(run["verdict"]["total"]) - int(run["round1"]["total"])
                for run in variant_runs
                if run.get("verdict")
                and run.get("round1")
                and run["verdict"].get("total") is not None
                and run["round1"].get("total") is not None
            ]
            delta_mean = _mean(deltas)
            if variant.expectation.defense_delta_min is not None:
                add(
                    case,
                    "defenseDeltaMin",
                    variant.expectation.defense_delta_min,
                    _round2(delta_mean),
                    delta_mean is not None and delta_mean >= variant.expectation.defense_delta_min,
                    variant.id,
                )
            if variant.expectation.defense_delta_max is not None:
                add(
                    case,
                    "defenseDeltaMax",
                    variant.expectation.defense_delta_max,
                    _round2(delta_mean),
                    delta_mean is not None and delta_mean <= variant.expectation.defense_delta_max,
                    variant.id,
                )

        contrast = expectation.contrast
        if contrast is not None:
            delta_by_variant: dict[str, float | None] = {}
            for variant in case.variants:
                deltas = [
                    int(run["verdict"]["total"]) - int(run["round1"]["total"])
                    for run in case_runs
                    if run.get("variantId") == variant.id
                    and run.get("verdict")
                    and run.get("round1")
                    and run["verdict"].get("total") is not None
                    and run["round1"].get("total") is not None
                ]
                delta_by_variant[variant.id] = _mean(deltas)
            better = delta_by_variant.get(contrast.better_variant)
            worse = delta_by_variant.get(contrast.worse_variant)
            gap = better - worse if better is not None and worse is not None else None
            add(
                case,
                "defenseContrastGap",
                contrast.min_gap,
                _round2(gap),
                gap is not None and gap >= contrast.min_gap,
            )
            if contrast.better_delta_min is not None:
                add(
                    case,
                    "betterVariantDeltaMin",
                    contrast.better_delta_min,
                    _round2(better),
                    better is not None and better >= contrast.better_delta_min,
                )
            if contrast.worse_delta_max is not None:
                add(
                    case,
                    "worseVariantDeltaMax",
                    contrast.worse_delta_max,
                    _round2(worse),
                    worse is not None and worse <= contrast.worse_delta_max,
                )

    discrimination = metrics["discrimination"]
    results.append(
        {
            "caseId": None,
            "variantId": None,
            "check": "discriminationOrder",
            "expected": f"each tier pair separated by at least {DISCRIMINATION_MARGIN} points",
            "actual": [
                {
                    "betterTier": check["betterTier"],
                    "worseTier": check["worseTier"],
                    "gap": check["gap"],
                    "ok": check["ok"],
                }
                for check in discrimination["order"]
            ],
            "ok": discrimination["passed"],
        }
    )
    return results


def collect_issues(
    cases: list[LionCase],
    runs: list[dict[str, Any]],
    expectations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    case_map = _case_meta(cases)
    issues: list[dict[str, Any]] = []

    def add(
        severity: str,
        kind: str,
        detail: str,
        *,
        case_id: str | None = None,
        variant_id: str | None = None,
        repeat: int | None = None,
        investor: str | None = None,
        quote: str | None = None,
    ) -> None:
        issues.append(
            {
                "severity": severity,
                "kind": kind,
                "caseId": case_id,
                "variantId": variant_id,
                "repeat": repeat,
                "investor": investor,
                "detail": detail,
                "quote": quote,
            }
        )

    for run in runs:
        case_id = str(run.get("caseId"))
        if run.get("status") != "done":
            add(
                "high",
                "session",
                f"{run.get('status')}: {run.get('error') or 'no detail'}",
                case_id=case_id,
                variant_id=run.get("variantId"),
                repeat=run.get("repeat"),
            )
            continue

        for phase in ("round1", "verdict"):
            for investor_id, entry in _entries(run, phase).items():
                text = str(entry.get("text") or "")
                if not text:
                    continue
                if any(term in text.lower() for term in META_TERMS):
                    add(
                        "high",
                        "meta-leak",
                        f"{phase} text mentions the setup or an AI role",
                        case_id=case_id,
                        variant_id=run.get("variantId"),
                        repeat=run.get("repeat"),
                        investor=investor_id,
                        quote=_excerpt(text),
                    )
                if phase == "verdict":
                    decision = entry.get("decision")
                    text_value = str(entry.get("text") or "")
                    if decision == "in" and not IN_PHRASE.search(text_value):
                        add(
                            "medium",
                            "decision-phrase",
                            "an in decision does not say I'm in",
                            case_id=case_id,
                            variant_id=run.get("variantId"),
                            repeat=run.get("repeat"),
                            investor=investor_id,
                            quote=_excerpt(text_value),
                        )
                    if decision == "out" and not OUT_PHRASE.search(text_value):
                        add(
                            "medium",
                            "decision-phrase",
                            "an out decision does not say I'm out",
                            case_id=case_id,
                            variant_id=run.get("variantId"),
                            repeat=run.get("repeat"),
                            investor=investor_id,
                            quote=_excerpt(text_value),
                        )

        for text_item in _texts(run):
            own, foreign = _lens_hits(text_item["text"], text_item["investor"])
            if own == 0 and foreign >= LENS_MIN_FOREIGN_HITS:
                add(
                    "low",
                    "lens-leak",
                    f"{text_item['investor']} text has no own-lens term and "
                    f"{foreign} foreign-lens terms",
                    case_id=case_id,
                    variant_id=run.get("variantId"),
                    repeat=run.get("repeat"),
                    investor=text_item["investor"],
                    quote=_excerpt(text_item["text"]),
                )
            words = len(text_item["text"].split())
            if text_item["kind"] in ("assessment", "statement") and words > 60:
                add(
                    "low",
                    "length",
                    f"{text_item['kind']} has {words} words (target about 40, limit 60)",
                    case_id=case_id,
                    variant_id=run.get("variantId"),
                    repeat=run.get("repeat"),
                    investor=text_item["investor"],
                    quote=_excerpt(text_item["text"]),
                )
            if text_item["kind"] == "question" and (
                words > 30 or len(QUESTION_MARK.findall(text_item["text"])) != 1
            ):
                add(
                    "low",
                    "length",
                    f"question has {words} words and "
                    f"{len(QUESTION_MARK.findall(text_item['text']))} question marks",
                    case_id=case_id,
                    variant_id=run.get("variantId"),
                    repeat=run.get("repeat"),
                    investor=text_item["investor"],
                    quote=_excerpt(text_item["text"]),
                )

        pitch = _pitch_text(run, case_map)
        for phase in ("round1", "verdict"):
            for investor_id, entry in _entries(run, phase).items():
                text = str(entry.get("text") or "")
                if not text:
                    continue
                source = (
                    pitch if phase == "round1" else f"{pitch} {_defense_text(run, investor_id)}"
                )
                _, unsupported_numbers = _number_checks(source, text)
                for number in unsupported_numbers:
                    add(
                        "medium",
                        "faithfulness",
                        f"{phase} cites number {number} that is not in the pitch or answer",
                        case_id=case_id,
                        variant_id=run.get("variantId"),
                        repeat=run.get("repeat"),
                        investor=investor_id,
                        quote=_excerpt(text),
                    )
                source_words = _content_words(source)
                for quote in _quotes(text):
                    words = _content_words(quote)
                    if not words:
                        continue
                    if len(words & source_words) / len(words) < 0.6:
                        add(
                            "medium",
                            "faithfulness",
                            f"{phase} quotes material not found in the pitch or answer",
                            case_id=case_id,
                            variant_id=run.get("variantId"),
                            repeat=run.get("repeat"),
                            investor=investor_id,
                            quote=_excerpt(quote, 160),
                        )

    for expectation in expectations:
        if expectation["ok"] is not False:
            continue
        add(
            "medium",
            "expectation",
            f"{expectation['check']}: expected {expectation['expected']}, "
            f"actual {expectation['actual']}",
            case_id=expectation.get("caseId"),
            variant_id=expectation.get("variantId"),
        )

    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda issue: (order.get(issue["severity"], 3), issue["kind"]))
    return issues
