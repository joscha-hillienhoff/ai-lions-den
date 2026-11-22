"""Server-side validation of structured jury output.

The JSON schema sent to the model deliberately contains no minimum, maximum,
minLength, maxLength or pattern keywords: Azure OpenAI's structured outputs do
not accept them. Limits are enforced here instead.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .types import Round1Output, VerdictOutput

# The prompts ask for about 40 words and a question of about 25 words. These
# limits stay deliberately generous so a valid answer that runs slightly long
# is kept rather than rejected.
ASSESSMENT_LIMIT = 900
QUESTION_LIMIT = 500
STATEMENT_LIMIT = 900

# The one threshold that turns an investor's final score into an investment decision.
INVESTMENT_SCORE_THRESHOLD = 7
# Every investor score is given on this scale.
INVESTOR_SCORE_MAX = 10
VERDICT_SCORE_SHIFT = 2

Decision = Literal["in", "out"]


def decision_for_score(score: int) -> Decision:
    """Derive the investment decision from the already clamped final score."""
    return "in" if score >= INVESTMENT_SCORE_THRESHOLD else "out"


class JuryOutputError(Exception):
    pass


def _tidy(value: str, limit: int) -> str:
    tidied = re.sub(r"\s+", " ", value).strip()
    if len(tidied) > limit:
        raise JuryOutputError(f"Invalid jury output: text exceeds {limit} characters")
    return tidied


def _validate_score(value: Any, *, bounded: bool = True) -> int:
    if isinstance(value, bool):
        raise ValueError("score must be an integer")
    if isinstance(value, str):
        raise ValueError("score must be an integer")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("score must be an integer")
        value = int(value)
    if not isinstance(value, int):
        raise ValueError("score must be an integer")
    if bounded and (value < 1 or value > INVESTOR_SCORE_MAX):
        raise ValueError(f"score must be between 1 and {INVESTOR_SCORE_MAX}")
    return value


class _Round1Schema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int
    assessment: str
    question: str

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, value: Any) -> int:
        return _validate_score(value)

    @field_validator("assessment", "question", mode="before")
    @classmethod
    def _string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value.strip()


class _VerdictSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    statement: str
    score: int

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, value: Any) -> int:
        return _validate_score(value, bounded=False)

    @field_validator("statement", mode="before")
    @classmethod
    def _string(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("must be a string")
        return value.strip()


def clamp_verdict_score(score: int, round1_score: int | None) -> int:
    """Keep a final score within two points of the investor's own round 1 score.

    The wire schema stays free of minimum, maximum and pattern keywords for
    Azure OpenAI compatibility, so the limit is enforced here after the model.
    """
    bounded = max(1, min(INVESTOR_SCORE_MAX, int(score)))
    if round1_score is None:
        return bounded
    return max(
        max(1, round1_score - VERDICT_SCORE_SHIFT),
        min(min(INVESTOR_SCORE_MAX, round1_score + VERDICT_SCORE_SHIFT), bounded),
    )


def parse_round1_output(raw: Any) -> Round1Output:
    try:
        parsed = _Round1Schema.model_validate(raw)
    except Exception as error:  # pydantic ValidationError
        raise JuryOutputError(f"Invalid round 1 output: {error}") from error
    assessment = _tidy(parsed.assessment, ASSESSMENT_LIMIT)
    question = _tidy(parsed.question, QUESTION_LIMIT)
    if not assessment or not question:
        raise JuryOutputError("Invalid round 1 output: empty text field")
    return Round1Output(score=parsed.score, assessment=assessment, question=question)


def parse_verdict_output(raw: Any) -> VerdictOutput:
    try:
        parsed = _VerdictSchema.model_validate(raw)
    except Exception as error:  # pydantic ValidationError
        raise JuryOutputError(f"Invalid verdict output: {error}") from error
    statement = _tidy(parsed.statement, STATEMENT_LIMIT)
    if not statement:
        raise JuryOutputError("Invalid verdict output: empty statement")
    return VerdictOutput(statement=statement, score=parsed.score)


def parse_json_content(content: str | None) -> Any:
    if not content:
        raise JuryOutputError("The model returned an empty response.")
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise JuryOutputError("The model returned content that is not valid JSON.") from error


ROUND1_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["score", "assessment", "question"],
    "properties": {
        "score": {"type": "integer"},
        "assessment": {
            "type": "string",
            "description": (
                "About 40 words and no more than 2 to 3 short sentences, naming the "
                "strongest point and the single biggest weakness inside this investor's lens."
            ),
        },
        "question": {
            "type": "string",
            "description": (
                "Exactly one question, at most about 25 words, with no second part joined "
                "by 'and' or a dash. The team answers it in at most 500 characters."
            ),
        },
    },
}

VERDICT_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["statement", "score"],
    "properties": {
        "statement": {
            "type": "string",
            "description": (
                "About 40 words and no more than 2 to 3 short sentences, judging the "
                "team's answer and one change on their own merits in this investor's own voice."
            ),
        },
        "score": {"type": "integer"},
    },
}
