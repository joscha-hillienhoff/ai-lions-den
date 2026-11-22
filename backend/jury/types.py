from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ..personas import Investor, InvestorId
from ..state import Pitch


@dataclass(frozen=True)
class Round1Output:
    score: int
    assessment: str
    question: str


@dataclass(frozen=True)
class VerdictOutput:
    statement: str
    score: int


@dataclass(frozen=True)
class Round1Input:
    team_id: str
    team_name: str
    pitch: Pitch
    investor: Investor


@dataclass(frozen=True)
class Round1Summary:
    investor_id: InvestorId
    investor_name: str
    score: int
    assessment: str
    question: str


@dataclass(frozen=True)
class VerdictInput:
    team_id: str
    team_name: str
    pitch: Pitch
    investor: Investor
    answer: str
    one_change: str
    round1: list[Round1Summary]
    previous: list[dict[str, object]]


class JuryProvider(Protocol):
    name: Literal["demo", "openai"]

    async def evaluate_round1(self, input_value: Round1Input) -> Round1Output: ...

    async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput: ...
