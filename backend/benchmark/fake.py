"""A deterministic fake jury for tests and end-to-end verification.

The fake never touches the network and never needs an API key. It reports token
usage so the budget guard and the cost metrics are exercised by the same code as
a paid run. Scores follow the case tier and rise by one point when a long,
specific defense answer is present, which makes every benchmark check reachable.
"""

from __future__ import annotations

from collections.abc import Callable

from ..jury.pricing import estimate_prompt_tokens, estimate_tokens
from ..jury.prompts import (
    round1_system_prompt,
    round1_user_prompt,
    verdict_system_prompt,
    verdict_user_prompt,
)
from ..jury.types import Round1Input, Round1Output, VerdictInput, VerdictOutput
from .cases import LionCase

TIER_SCORES = {
    "strong": 8,
    "mixed": 6,
    "one-sided": 5,
    "weak": 4,
    "troll": 3,
    "manipulation": 3,
}

ASSESSMENTS = {
    "growth": (
        "The market story for {startup} is the part I can judge. You name a starting "
        "niche, but I still need the expansion mechanism that turns it into a much "
        "larger category with a repeatable route to customers."
    ),
    "finance": (
        "The margin logic for {startup} is what I can test. Your revenue idea needs a "
        "price, a cost to serve and a payback period before the plan holds together "
        "under growth."
    ),
    "customer": (
        "The customer story for {startup} is clear enough to examine. Someone has a "
        "painful problem, but interest is not demand until a real buyer changes "
        "behaviour and pays for the alternative."
    ),
    "technology": (
        "The technical case for {startup} needs one more test. Describe the failure "
        "that would make the product unusable and the evaluation that catches it, "
        "then show why the data and integration stay defensible."
    ),
}

QUESTIONS = {
    "growth": "Which concrete milestone in twelve months would show the next market is reachable?",
    "finance": "Which cost assumption would you validate first with a real paying customer?",
    "customer": "What evidence from a real customer would show that switching is worth the effort?",
    "technology": (
        "How would you prove the data and integration stay defensible against a provider?"
    ),
}

IN_STATEMENTS = {
    "growth": (
        "The growth path you describe turns the first niche into a testable next market, "
        "and the one change narrows the risk. That is enough for a first yes. I'm in."
    ),
    "finance": (
        "The margin and payback you describe give me assumptions I can check, and the "
        "one change limits the capital risk. That is enough for a first yes. I'm in."
    ),
    "customer": (
        "The customer evidence you describe goes beyond interest, and the one change "
        "makes adoption easier to test. That is enough for a first yes. I'm in."
    ),
    "technology": (
        "The technical proof and data story you describe address the main failure, and "
        "the one change keeps the scope honest. That is enough for a first yes. I'm in."
    ),
}

OUT_STATEMENTS = {
    "growth": (
        "The market story you tell stays at the level of intent, and the one change does "
        "not show how the next segment arrives. I need firmer evidence before I commit. "
        "I'm out."
    ),
    "finance": (
        "The margin story you tell stays at the level of intent, and the one change does "
        "not fix the missing payback case. I need firmer numbers before I commit. I'm out."
    ),
    "customer": (
        "The customer story you tell stays at the level of intent, and the one change does "
        "not prove that a buyer will switch. I need firmer evidence before I commit. I'm out."
    ),
    "technology": (
        "The technical story you tell stays at the level of intent, and the one change does "
        "not name the failure or the evaluation. I need firmer proof before I commit. I'm out."
    ),
}

SPECIFIC_ANSWER_CHARS = 250


class FakeJuryProvider:
    """Tier-aware fake output with the same call shape as the real provider."""

    name = "fake"

    def __init__(
        self,
        cases: list[LionCase],
        *,
        on_usage: Callable[[int, int], None] | None = None,
    ) -> None:
        self._score_by_startup = {case.pitch.startup_name: TIER_SCORES[case.tier] for case in cases}
        self._on_usage = on_usage
        self.calls = 0

    async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
        investor = input_value.investor
        score = self._score_by_startup.get(input_value.pitch.startup_name, 5)
        assessment = ASSESSMENTS[investor.id].format(startup=input_value.pitch.startup_name)
        question = QUESTIONS[investor.id]
        self._report(
            prompt=round1_system_prompt(investor)
            + round1_user_prompt(team_name=input_value.team_name, pitch=input_value.pitch),
            completion=assessment + " " + question,
        )
        return Round1Output(score=score, assessment=assessment, question=question)

    async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
        own = next(
            (entry for entry in input_value.round1 if entry.investor_id == input_value.investor.id),
            None,
        )
        round1_score = own.score if own is not None else 5
        answer = input_value.answer.strip()
        specific = len(answer) >= SPECIFIC_ANSWER_CHARS and any(
            character.isdigit() for character in answer
        )
        score = min(10, round1_score + (1 if specific else 0))
        pool = IN_STATEMENTS if score >= 7 else OUT_STATEMENTS
        statement = pool[input_value.investor.id]
        self._report(
            prompt=verdict_system_prompt(input_value.investor) + verdict_user_prompt(input_value),
            completion=statement,
        )
        return VerdictOutput(statement=statement, score=score)

    def _report(self, *, prompt: str, completion: str) -> None:
        self.calls += 1
        if self._on_usage is not None:
            self._on_usage(estimate_prompt_tokens(prompt), estimate_tokens(completion))
