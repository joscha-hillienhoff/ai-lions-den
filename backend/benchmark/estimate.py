"""Pre-run call and cost estimate for the lion quality benchmark.

The estimate builds the real prompt text for every call, so the input side is
close to what the model will see. Completion tokens are a fixed conservative
allowance per call; retries are billable attempts beyond this no-retry estimate.
The hard budget guard in :mod:`backend.benchmark.meter` is what protects spend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..jury.pricing import (
    MAX_JURY_COMPLETION_TOKENS,
    ModelPrice,
    cost_usd,
    estimate_prompt_tokens,
)
from ..jury.prompts import (
    round1_system_prompt,
    round1_user_prompt,
    verdict_system_prompt,
    verdict_user_prompt,
)
from ..jury.types import Round1Summary, VerdictInput
from ..jury.validation import ROUND1_JSON_SCHEMA, VERDICT_JSON_SCHEMA
from ..personas import INVESTOR_IDS, investor_by_id
from .cases import DefenseVariant, LionCase

# The benchmark caps every call at the largest output the validators accept
# (900 characters, German included, plus JSON structure). Estimating every call
# at that cap keeps the estimate an upper bound.
ROUND1_COMPLETION_TOKENS = MAX_JURY_COMPLETION_TOKENS
VERDICT_COMPLETION_TOKENS = MAX_JURY_COMPLETION_TOKENS

# Representative round 1 material for the verdict prompt. The score is the
# midpoint, and the text matches the requested output length.
_PLACEHOLDER_SCORE = 5
_PLACEHOLDER_ASSESSMENT = (
    "The strongest point you show is a focused first customer with a concrete problem, "
    "while the biggest weakness in this lens is the missing evidence for how that problem "
    "turns into a repeatable and defensible position at the scale you describe."
)
_PLACEHOLDER_QUESTION = (
    "Which assumption about the first customer segment would you test in the next eight weeks?"
)


@dataclass(frozen=True)
class PlanEstimate:
    sessions: int
    calls: int
    prompt_tokens: int
    completion_tokens: int
    usd: float


def estimate_session(case: LionCase, variant: DefenseVariant) -> tuple[int, int]:
    """Return (prompt tokens, completion tokens) for one simulated session."""
    pitch = case.pitch.to_pitch()
    prompt_tokens = 0
    completion_tokens = 0

    for investor_id in INVESTOR_IDS:
        investor = investor_by_id(investor_id)
        prompt = "\n".join(
            [
                round1_system_prompt(investor),
                round1_user_prompt(team_name=case.pitch.startup_name, pitch=pitch),
            ]
        )
        prompt_tokens += estimate_prompt_tokens(
            prompt + json.dumps(ROUND1_JSON_SCHEMA, separators=(",", ":"))
        )
        completion_tokens += ROUND1_COMPLETION_TOKENS

    round1 = [
        Round1Summary(
            investor_id=investor_id,
            investor_name=investor_by_id(investor_id).name,
            score=_PLACEHOLDER_SCORE,
            assessment=_PLACEHOLDER_ASSESSMENT,
            question=_PLACEHOLDER_QUESTION,
        )
        for investor_id in INVESTOR_IDS
    ]
    for investor_id in INVESTOR_IDS:
        investor = investor_by_id(investor_id)
        input_value = VerdictInput(
            team_id=f"{case.id}-{variant.id}",
            team_name=case.pitch.startup_name,
            pitch=pitch,
            investor=investor,
            answer=variant.answers[investor_id],
            one_change=variant.one_change,
            round1=round1,
            previous=[],
        )
        prompt = "\n".join(
            [
                verdict_system_prompt(investor),
                verdict_user_prompt(input_value),
            ]
        )
        prompt_tokens += estimate_prompt_tokens(
            prompt + json.dumps(VERDICT_JSON_SCHEMA, separators=(",", ":"))
        )
        completion_tokens += VERDICT_COMPLETION_TOKENS

    return prompt_tokens, completion_tokens


def estimate_plan(cases: list[LionCase], repeats: int, price: ModelPrice) -> PlanEstimate:
    sessions = sum(len(case.variants) for case in cases) * repeats
    prompt_tokens = 0
    completion_tokens = 0
    for case in cases:
        for variant in case.variants:
            prompt, completion = estimate_session(case, variant)
            prompt_tokens += prompt * repeats
            completion_tokens += completion * repeats
    return PlanEstimate(
        sessions=sessions,
        calls=sessions * 2 * len(INVESTOR_IDS),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=cost_usd(price, prompt_tokens, completion_tokens),
    )
