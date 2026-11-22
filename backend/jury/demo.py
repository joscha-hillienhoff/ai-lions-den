"""Deterministic demo jury: no network, no key, no cost."""

from __future__ import annotations

import asyncio
import math
import re
from typing import Literal

from ..personas import InvestorId
from .types import Round1Input, Round1Output, VerdictInput, VerdictOutput
from .validation import decision_for_score

_PersonaDemo = dict[str, list[str]]

DEMO: dict[InvestorId, _PersonaDemo] = {
    "growth": {
        "assessments": [
            "The ambition behind {startup} gets my attention. A large market is only the opening argument; I still need a disruptive model and a repeatable route to customers. Show me what turns this first niche into a category worth billions.",
            "I like a focused starting market, but I invest in the expansion beyond it. Your pitch needs to connect the first hundred customers to the next ten thousand. What compounds as you grow, beyond the size of the sales team?",
            "A useful AI feature can win applause without becoming a venture-scale company. The opportunity needs a sharper reason for customers to choose you over interchangeable alternatives. Give me a market size and the advantage that lets you capture it.",
            "An exponential growth story needs a mechanism, not an adjective. If each customer helps attract the next, show me how that loop works. Then tell me which milestone proves it can scale outside your initial segment.",
            "The starting niche for {startup} is worth exploring. My excitement depends on what opens up after you win it, and why this team can move faster than incumbents. Ambition earns the meeting; a credible expansion plan earns the investment.",
        ],
        "questions": [
            "Which repeatable acquisition channel could take you to 10,000 customers, and what evidence supports that scale?",
            "What makes your model disruptive enough to win a large market instead of remaining a useful AI add-on?",
            "Which measurable milestone in twelve months would show that this can become a billion-dollar company?",
            "What changes as you enter your second market that makes growth faster rather than more expensive?",
        ],
        "inStatements": [
            "{prev} is right to ask for proof, and the expansion risk remains. Still, {startup} offers an ambitious starting point in a market large enough to justify a venture bet. I am in, with repeatable distribution as the next milestone.",
            "The opportunity is big enough to matter, and the answers give the growth story a clearer direction. I want the next investment to accelerate a repeatable channel. I am in.",
            "I can see a path from this focused niche to a much larger market. The answers give me enough confidence to test that expansion with this team. I am in.",
        ],
        "outStatements": [
            "The answers sharpened the idea but did not show a path to exponential growth. I still see a useful feature competing in a crowded market. I am out until a repeatable channel changes that picture.",
            "The ambition is attractive, but the expansion plan still depends on founders doing everything themselves. That does not support the scale I look for in a venture investment. I am out.",
            "{prev} framed the risk well, and I agree: there is no evidence yet that this can grow beyond a narrow AI feature. A billion-dollar outcome needs a stronger model and a larger reachable market. I am out.",
        ],
    },
    "finance": {
        "assessments": [
            "Let us put the story aside and examine who pays. I need a price, a cost to serve and a customer acquisition cost before I can assess financial viability. A 2.5 million EUR valuation needs more than a polished forecast.",
            "The revenue idea for {startup} is a starting point. The real question is what remains after acquisition, delivery and retention costs. Show me why the margin stays attractive as competition arrives.",
            "You are asking for 250,000 EUR for 10 %, implying a 2.5 million EUR post-money valuation. I want to know which commercial milestone that capital buys. If profitability requires another cheque first, make the funding need explicit.",
            "A convincing story does not settle the break-even calculation. Name the average contract value, contribution margin and number of customers needed to cover fixed costs. Then show whether the available capital lasts long enough.",
            "Recurring revenue is attractive only when customers stay and service costs remain controlled. Your model needs a clear payback period and a credible path to profit. I will judge the cash requirements before the growth forecast.",
        ],
        "questions": [
            "Who pays, what does it cost to acquire that customer, and how many months until the acquisition cost is recovered?",
            "What does the 250,000 EUR fund, and how much additional capital would you need before profitability?",
            "How many paying customers do you need to break even, and can you reach that number before cash runs out?",
            "What protects your gross margin when model costs, service demands or competitive pricing change?",
        ],
        "inStatements": [
            "The answers make the revenue model and capital requirements more concrete. The margin still needs testing, but the path to financial viability is clear enough for a first investment. I am in.",
            "I see a commercial experiment worth funding if the 250,000 EUR is tied to customer payback and cash milestones. The story is useful only because the numbers can now be tested. I am in.",
            "{prev} is right to question the forecast. The answers give me enough structure around monetization and acquisition costs to test whether the margin holds. I am in.",
        ],
        "outStatements": [
            "{prev} raised the right concern, and I still cannot reconcile the cash needs with the proposed valuation. A revenue forecast does not establish profitability. I am out.",
            "The acquisition cost and customer lifetime remain too uncertain for {startup}. Without a credible payback period, more growth could simply consume more capital. I am out.",
            "The presentation is impressive, but I cannot find a durable margin in the answers. I need evidence that customers pay enough to cover delivery and acquisition. At this valuation, I am out.",
        ],
    },
    "customer": {
        "assessments": [
            "Imagine the customer deciding whether to change a familiar workflow. The problem may be frustrating, but frustration alone does not prove willingness to pay. Tell me what real buyers have done, rather than what you hope they will do.",
            "The customer story for {startup} is understandable. I still need evidence that the existing workaround hurts enough to justify switching. A clever technology does not establish product-market fit.",
            "A specific customer segment is a useful start. How do you reach those people, and who has the authority and budget to buy? I want evidence of demand from buyers you can actually access.",
            "The problem deserves investigation, but the user and the buyer may be different people. Name who signs the cheque and what alternative they would stop paying for. That is where willingness to pay becomes concrete.",
            "I can picture the proposed benefit, but I cannot yet separate interest from demand. Show me a customer commitment, a paid trial or another credible signal of purchase intent. Compliments about the technology are not enough.",
        ],
        "questions": [
            "Which customer has shown willingness to pay, and what did they do that goes beyond expressing interest?",
            "What do your customers use today, and what evidence shows they would switch to your product?",
            "How will you reach the first ten buyers, and which budget pays for the problem you solve?",
            "What did your last five customer conversations reveal that changed your product or target segment?",
        ],
        "inStatements": [
            "{prev} is right that the evidence is early. The answers give me a specific customer problem and a plausible route to a buyer who can pay. I am in, with demand validation as the next step.",
            "The answers connect a real workflow problem to an identifiable buyer. I would want more customer evidence before scaling, but there is enough here for an angel investment. I am in.",
            "The case for {startup} is strongest when you describe the customer rather than the technology. The proposed path to adoption is concrete enough to test with real buyers. I am in.",
        ],
        "outStatements": [
            "I understand the problem, but I still do not know who will pay to solve it. Customer enthusiasm is not the same as a purchasing decision. I am out until that gap is closed.",
            "{prev} put it well: the evidence is early. I need a credible sign of demand and a practical way to reach buyers before calling this product-market fit. I am out.",
            "The answers describe an interesting technology without enough proof that customers would leave their current solution. Competition includes doing nothing, and that alternative still looks easy. I am out.",
        ],
    },
    "technology": {
        "assessments": [
            "A working demo and a defensible AI business are different claims. The pitch needs to establish reliable performance, access to useful data and an advantage beyond the underlying model. What would remain yours if a provider shipped the same capability?",
            "The technical idea behind {startup} deserves a feasibility test. GenAI can produce plausible errors, so name the failure that would matter most to your customer. Show how you would measure it before deployment.",
            "A model API can accelerate the first version. It also creates dependence on another company for pricing, availability and capabilities. Explain what happens to the product if that provider changes its terms or ships your key feature.",
            "The data argument needs more precision. Access, usage rights and a repeatable way to improve outcomes matter more than simply collecting records. Show which part competitors cannot obtain or reproduce.",
            "AI can make this possible in a way that was out of reach a few years ago, so the first question is whether it sits at the core of {startup} or is only decoration. If it is the core, show how you would measure its quality. If it is decoration, say so plainly.",
            "The proposed technology may be feasible, but feasibility alone does not create differentiation. I want to separate your own technical advantage from capabilities available to every model customer. That distinction determines whether this is defensible.",
        ],
        "questions": [
            "Which GenAI failure would make this product unusable, and what evaluation would expose it before launch?",
            "What data can you legally access that competitors cannot, and how does it improve measurable customer outcomes?",
            "If your model provider changes pricing or removes a capability, how does your product remain viable?",
            "What advantage remains if OpenAI adds your core capability to its standard offering?",
        ],
        "inStatements": [
            "{prev} identifies a real risk. The answers make the feasibility test clearer and give me a useful starting point for examining data access and defensibility. I am in, with those technical assumptions still to validate.",
            "The team has made the main model limitation and the next evaluation concrete. Provider dependence remains a risk, but there is a plausible product beyond a thin interface. I am in.",
            "I would not treat the underlying model as proprietary advantage. The answers give enough direction on workflow integration and data access to test a more durable position. I am in.",
        ],
        "outStatements": [
            "The core capability is available to other model customers, and the pitch has not established unique data access. A provider could absorb too much of this product. I am out.",
            "{prev} is right to question the moat. The answers describe implementation effort without establishing differentiation or reducing dependence on the model provider. I am out.",
            "The first release may be buildable, but the reliability and data assumptions remain untested. I cannot underwrite defensibility while those foundations are still uncertain. I am out.",
        ],
    },
}


def _hash_string(value: str) -> int:
    """Small deterministic string hash (FNV-1a)."""
    result = 0x811C9DC5
    for character in value:
        result ^= ord(character)
        result = (result * 0x01000193) & 0xFFFFFFFF
    return result


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _excerpt(value: str, limit: int = 120) -> str:
    text = " ".join(value.split())
    text = re.sub(r"[.!?]+(?=(?:[\"'”’)\]},;:]*)(?:\s|$))", "", text)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class DemoJuryProvider:
    name: Literal["demo"] = "demo"

    def __init__(self, base_delay_ms: float) -> None:
        self._base_delay_ms = base_delay_ms

    async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
        persona = DEMO[input_value.investor.id]
        pitch = input_value.pitch
        seed = _hash_string(
            f"{input_value.team_id}:{input_value.investor.id}:{pitch.startup_name}:{pitch.tagline}"
        )
        await asyncio.sleep(self._delay(seed) / 1000)

        text_length = len(
            " ".join(
                [
                    pitch.startup_name,
                    pitch.tagline,
                    pitch.problem,
                    pitch.solution,
                    pitch.customers,
                    pitch.business_model,
                ]
            )
        )
        score = 4 + (seed % 5)
        if text_length > 1_500:
            score += 1
        if text_length < 500:
            score -= 1
        score = _clamp(score, 2, 9)

        assessments = persona["assessments"]
        questions = persona["questions"]
        return Round1Output(
            score=score,
            assessment=assessments[seed % len(assessments)].replace(
                "{startup}", pitch.startup_name
            ),
            question=questions[(seed >> 3) % len(questions)],
        )

    async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
        persona = DEMO[input_value.investor.id]
        seed = _hash_string(
            f"{input_value.team_id}:{input_value.investor.id}:verdict:"
            f"{input_value.answer}:{input_value.one_change}"
        )
        await asyncio.sleep(self._delay(seed) / 1000)

        own_index = next(
            (
                index
                for index, entry in enumerate(input_value.round1)
                if entry.investor_id == input_value.investor.id
            ),
            0,
        )
        own = input_value.round1[own_index] if own_index < len(input_value.round1) else None
        own_answer = input_value.answer or "(no answer)"
        one_change = input_value.one_change or "(no one change)"

        round1_score = own.score if own else 5
        movement = (seed % 5) - 2
        if len(own_answer.strip()) < 60:
            movement = min(movement, 0)
        if len(one_change.strip()) >= 80:
            movement += 1
        if len(own_answer.strip()) >= 180:
            movement += 1
        movement = _clamp(movement, -2, 2)
        score = _clamp(round1_score + movement, max(1, round1_score - 2), min(10, round1_score + 2))
        decision = decision_for_score(score)

        pool = persona["inStatements"] if decision == "in" else persona["outStatements"]
        previous_name = (
            str(input_value.previous[-1]["investorName"]) if input_value.previous else "The room"
        )
        voice = (
            pool[seed % len(pool)]
            .replace("{startup}", input_value.pitch.startup_name)
            .replace("{prev}", previous_name)
        )
        answer_excerpt = _excerpt(own_answer)
        change_excerpt = _excerpt(one_change)
        if score > round1_score:
            movement_sentence = (
                f'Your answer, "{answer_excerpt}", and your one change, "{change_excerpt}", '
                "explain why my score rose."
            )
        elif score < round1_score:
            movement_sentence = (
                f'Your answer, "{answer_excerpt}", and your one change, "{change_excerpt}", '
                "explain why my score fell."
            )
        else:
            movement_sentence = (
                f'Your answer, "{answer_excerpt}", and your one change, "{change_excerpt}", '
                "explain why my score held."
            )
        statement = f"{movement_sentence} {voice}"
        return VerdictOutput(statement=statement, score=score)

    def _delay(self, seed: int) -> float:
        factor = 0.55 + ((seed % 100) / 100) * 0.9
        return max(0, math.floor(self._base_delay_ms * factor + 0.5))
