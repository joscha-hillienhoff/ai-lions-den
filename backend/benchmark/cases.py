"""Loading and validation of the lion quality benchmark test set."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..jury.validation import INVESTOR_SCORE_MAX, VERDICT_SCORE_SHIFT
from ..personas import INVESTOR_IDS, InvestorId
from ..pitch import validate_pitch
from ..state import DEFENSE_ANSWER_LIMIT, DEFENSE_CHANGE_LIMIT, Pitch

Tier = Literal["strong", "mixed", "weak", "one-sided", "troll", "manipulation"]
TIERS: tuple[Tier, ...] = ("strong", "mixed", "weak", "one-sided", "troll", "manipulation")
VariantKind = Literal["fix", "dodge", "neutral"]
TOTAL_SCORE_MIN = len(INVESTOR_IDS)
TOTAL_SCORE_MAX = INVESTOR_SCORE_MAX * len(INVESTOR_IDS)
DELTA_MIN = -VERDICT_SCORE_SHIFT * len(INVESTOR_IDS)
DELTA_MAX = VERDICT_SCORE_SHIFT * len(INVESTOR_IDS)
GAP_MAX = DELTA_MAX - DELTA_MIN

DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent.parent.parent / "benchmarks" / "lion_quality_cases.json"
)


class CaseFileError(ValueError):
    pass


@dataclass(frozen=True)
class PitchData:
    startup_name: str
    tagline: str
    problem: str
    solution: str
    customers: str
    business_model: str

    def to_pitch(self) -> Pitch:
        return Pitch(
            startup_name=self.startup_name,
            tagline=self.tagline,
            problem=self.problem,
            solution=self.solution,
            customers=self.customers,
            business_model=self.business_model,
        )


@dataclass(frozen=True)
class VariantExpectation:
    defense_delta_min: float | None = None
    defense_delta_max: float | None = None


@dataclass(frozen=True)
class DefenseVariant:
    id: str
    kind: VariantKind
    answers: dict[InvestorId, str]
    one_change: str
    expectation: VariantExpectation = field(default_factory=VariantExpectation)


@dataclass(frozen=True)
class ContrastExpectation:
    better_variant: str
    worse_variant: str
    min_gap: float
    better_delta_min: float | None = None
    worse_delta_max: float | None = None


@dataclass(frozen=True)
class CaseExpectation:
    mean_total_min: float | None = None
    mean_total_max: float | None = None
    in_rate_min: float | None = None
    in_rate_max: float | None = None
    forbidden_phrases: tuple[str, ...] = ()
    contrast: ContrastExpectation | None = None


@dataclass(frozen=True)
class LionCase:
    id: str
    tier: Tier
    language: str
    tags: tuple[str, ...]
    pitch: PitchData
    variants: tuple[DefenseVariant, ...]
    expectation: CaseExpectation


def _required(payload: dict, key: str, where: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CaseFileError(f"{where}: {key} must be a non-empty string")
    return value.strip()


def _finite_float(value: object, key: str, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaseFileError(f"{where}: {key} must be a number")
    try:
        result = float(value)
    except OverflowError as error:
        raise CaseFileError(f"{where}: {key} must be finite") from error
    if not math.isfinite(result):
        raise CaseFileError(f"{where}: {key} must be finite")
    return result


def _optional_float(payload: dict, key: str, where: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    return _finite_float(value, key, where)


def _validate_bounds(
    minimum: float | None,
    maximum: float | None,
    *,
    minimum_key: str,
    maximum_key: str,
    lower: float,
    upper: float,
    where: str,
) -> None:
    for value, key in ((minimum, minimum_key), (maximum, maximum_key)):
        if value is not None and not lower <= value <= upper:
            raise CaseFileError(f"{where}: {key} must be between {lower:g} and {upper:g}")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise CaseFileError(f"{where}: {minimum_key} must not exceed {maximum_key}")


def _load_variant(payload: object, where: str) -> DefenseVariant:
    if not isinstance(payload, dict):
        raise CaseFileError(f"{where}: variant must be an object")
    variant_id = _required(payload, "id", where)
    kind = payload.get("kind", "neutral")
    if kind not in ("fix", "dodge", "neutral"):
        raise CaseFileError(f"{where}: kind must be fix, dodge or neutral")

    raw_answers = payload.get("answers")
    if not isinstance(raw_answers, dict):
        raise CaseFileError(f"{where}: answers must be an object keyed by investor id")
    unknown = set(raw_answers) - set(INVESTOR_IDS)
    missing = set(INVESTOR_IDS) - set(raw_answers)
    if unknown or missing:
        raise CaseFileError(
            f"{where}: answers must cover exactly {', '.join(INVESTOR_IDS)} "
            f"(missing: {sorted(missing)}, unknown: {sorted(unknown)})"
        )
    answers: dict[InvestorId, str] = {}
    for investor_id in INVESTOR_IDS:
        answer = raw_answers[investor_id]
        if not isinstance(answer, str) or not answer.strip():
            raise CaseFileError(f"{where}: answer for {investor_id} must be a non-empty string")
        if len(answer) > DEFENSE_ANSWER_LIMIT:
            raise CaseFileError(
                f"{where}: answer for {investor_id} exceeds {DEFENSE_ANSWER_LIMIT} characters"
            )
        answers[investor_id] = answer.strip()

    one_change = _required(payload, "oneChange", where)
    if len(one_change) > DEFENSE_CHANGE_LIMIT:
        raise CaseFileError(f"{where}: oneChange exceeds {DEFENSE_CHANGE_LIMIT} characters")

    raw_expect = payload.get("expect", {})
    if not isinstance(raw_expect, dict):
        raise CaseFileError(f"{where}: expect must be an object")
    defense_delta_min = _optional_float(raw_expect, "defenseDeltaMin", where)
    defense_delta_max = _optional_float(raw_expect, "defenseDeltaMax", where)
    _validate_bounds(
        defense_delta_min,
        defense_delta_max,
        minimum_key="defenseDeltaMin",
        maximum_key="defenseDeltaMax",
        lower=DELTA_MIN,
        upper=DELTA_MAX,
        where=where,
    )
    expectation = VariantExpectation(
        defense_delta_min=defense_delta_min,
        defense_delta_max=defense_delta_max,
    )
    return DefenseVariant(
        id=variant_id,
        kind=kind,
        answers=answers,
        one_change=one_change,
        expectation=expectation,
    )


def _load_contrast(payload: object, where: str) -> ContrastExpectation | None:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise CaseFileError(f"{where}: contrast must be an object")
    better_variant = _required(payload, "betterVariant", where)
    worse_variant = _required(payload, "worseVariant", where)
    min_gap = _finite_float(payload.get("minGap", 1.0), "minGap", where)
    better_delta_min = _optional_float(payload, "betterDeltaMin", where)
    worse_delta_max = _optional_float(payload, "worseDeltaMax", where)
    if not 0 < min_gap <= GAP_MAX:
        raise CaseFileError(f"{where}: minGap must be greater than 0 and at most {GAP_MAX:g}")
    _validate_bounds(
        better_delta_min,
        None,
        minimum_key="betterDeltaMin",
        maximum_key="betterDeltaMin",
        lower=DELTA_MIN,
        upper=DELTA_MAX,
        where=where,
    )
    _validate_bounds(
        None,
        worse_delta_max,
        minimum_key="worseDeltaMax",
        maximum_key="worseDeltaMax",
        lower=DELTA_MIN,
        upper=DELTA_MAX,
        where=where,
    )
    return ContrastExpectation(
        better_variant=better_variant,
        worse_variant=worse_variant,
        min_gap=min_gap,
        better_delta_min=better_delta_min,
        worse_delta_max=worse_delta_max,
    )


def _load_case(payload: object, index: int) -> LionCase:
    where = f"case {index + 1}"
    if not isinstance(payload, dict):
        raise CaseFileError(f"{where}: case must be an object")
    case_id = _required(payload, "id", where)
    where = f"case {case_id!r}"
    tier = payload.get("tier")
    if tier not in TIERS:
        raise CaseFileError(f"{where}: tier must be one of {', '.join(TIERS)}")
    language = payload.get("language", "en")
    if not isinstance(language, str) or len(language) != 2:
        raise CaseFileError(f"{where}: language must be a two-letter code")

    raw_pitch = payload.get("pitch")
    if not isinstance(raw_pitch, dict):
        raise CaseFileError(f"{where}: pitch must be an object")
    for key in ("startupName", "tagline", "problem", "solution", "customers", "businessModel"):
        _required(raw_pitch, key, where)
    validation = validate_pitch(raw_pitch)
    if not validation.ok or validation.pitch is None:
        details = "; ".join(
            f"{key}: {message}" for key, message in (validation.errors or {}).items()
        )
        raise CaseFileError(f"{where}: invalid pitch ({details})")
    validated = validation.pitch
    pitch = PitchData(
        startup_name=validated.startup_name,
        tagline=validated.tagline,
        problem=validated.problem,
        solution=validated.solution,
        customers=validated.customers,
        business_model=validated.business_model,
    )

    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise CaseFileError(f"{where}: variants must be a non-empty list")
    variants = tuple(
        _load_variant(item, f"{where} variant {position + 1}")
        for position, item in enumerate(raw_variants)
    )
    if len({variant.id for variant in variants}) != len(variants):
        raise CaseFileError(f"{where}: variant ids must be unique")

    raw_expect = payload.get("expect", {})
    if not isinstance(raw_expect, dict):
        raise CaseFileError(f"{where}: expect must be an object")
    raw_forbidden = raw_expect.get("forbiddenPhrases", [])
    if not isinstance(raw_forbidden, list) or not all(
        isinstance(phrase, str) and phrase.strip() for phrase in raw_forbidden
    ):
        raise CaseFileError(f"{where}: forbiddenPhrases must be a list of strings")
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise CaseFileError(f"{where}: tags must be a list of strings")

    mean_total_min = _optional_float(raw_expect, "meanTotalMin", where)
    mean_total_max = _optional_float(raw_expect, "meanTotalMax", where)
    in_rate_min = _optional_float(raw_expect, "inRateMin", where)
    in_rate_max = _optional_float(raw_expect, "inRateMax", where)
    _validate_bounds(
        mean_total_min,
        mean_total_max,
        minimum_key="meanTotalMin",
        maximum_key="meanTotalMax",
        lower=TOTAL_SCORE_MIN,
        upper=TOTAL_SCORE_MAX,
        where=where,
    )
    _validate_bounds(
        in_rate_min,
        in_rate_max,
        minimum_key="inRateMin",
        maximum_key="inRateMax",
        lower=0.0,
        upper=1.0,
        where=where,
    )
    contrast = _load_contrast(raw_expect.get("contrast"), where)
    variant_ids = {variant.id for variant in variants}
    if contrast is not None and (
        contrast.better_variant not in variant_ids
        or contrast.worse_variant not in variant_ids
        or contrast.better_variant == contrast.worse_variant
    ):
        raise CaseFileError(f"{where}: contrast variants must be two distinct case variants")
    expectation = CaseExpectation(
        mean_total_min=mean_total_min,
        mean_total_max=mean_total_max,
        in_rate_min=in_rate_min,
        in_rate_max=in_rate_max,
        forbidden_phrases=tuple(phrase.strip() for phrase in raw_forbidden),
        contrast=contrast,
    )
    return LionCase(
        id=case_id,
        tier=tier,
        language=language,
        tags=tuple(tags),
        pitch=pitch,
        variants=variants,
        expectation=expectation,
    )


def load_cases(path: str | Path | None = None) -> list[LionCase]:
    cases_path = Path(path) if path is not None else DEFAULT_CASES_PATH
    try:
        payload = json.loads(cases_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CaseFileError(f"Case file not found: {cases_path}") from error
    except json.JSONDecodeError as error:
        raise CaseFileError(f"Case file is not valid JSON: {error}") from error

    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise CaseFileError("Case file must contain a 'cases' list")
    cases = [_load_case(item, index) for index, item in enumerate(payload["cases"])]
    if len({case.id for case in cases}) != len(cases):
        raise CaseFileError("Case ids must be unique")
    return cases


def select_cases(cases: list[LionCase], only: list[str] | None) -> list[LionCase]:
    if not only:
        return cases
    wanted = {case_id.strip() for case_id in only if case_id.strip()}
    selected = [case for case in cases if case.id in wanted]
    missing = wanted - {case.id for case in selected}
    if missing:
        raise CaseFileError(f"Unknown case ids: {', '.join(sorted(missing))}")
    return selected
