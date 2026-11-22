"""Model prices for the jury quality benchmark.

This is the single place to keep per-model prices. They are list prices in US
dollars per one million tokens and are used only to estimate and cap benchmark
spend; the app itself never needs them.

Azure OpenAI deployments are often named after the deployment rather than the
model. The benchmark's ``--price-model`` option selects one of these keys when
``OPENAI_MODEL`` holds a deployment name.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date

# The preflight guard refuses a prompt above this estimate rather than cutting it, so it
# must stay above the largest verdict prompt a full-length ASCII pitch, answer and one
# change produce (about 16,900 by estimate_prompt_tokens). tests/test_text_limits.py
# fails if a limit grows past it.
MAX_JURY_PROMPT_TOKENS = 18_000
MAX_JURY_COMPLETION_TOKENS = 450


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


class UnknownModelPrice(KeyError):
    def __init__(self, model: str) -> None:
        self.model = model
        supported = ", ".join(sorted(PRICES))
        super().__init__(
            f"No price is configured for model {model!r}. Add it to "
            f"backend/jury/pricing.py or pass --price-model. Known models: {supported}"
        )


PRICES: dict[str, ModelPrice] = {
    "gpt-4.1": ModelPrice(input_per_million=2.00, output_per_million=8.00),
    "gpt-4.1-mini": ModelPrice(input_per_million=0.40, output_per_million=1.60),
    "gpt-4.1-nano": ModelPrice(input_per_million=0.10, output_per_million=0.40),
    "gpt-4o": ModelPrice(input_per_million=2.50, output_per_million=10.00),
    "gpt-4o-mini": ModelPrice(input_per_million=0.15, output_per_million=0.60),
}


def price_for(model: str) -> ModelPrice:
    """Resolve a model name, including dated snapshots such as 2025-04-14."""
    normalised = model.strip().lower()
    if normalised in PRICES:
        return PRICES[normalised]
    matches = []
    for key in PRICES:
        if not normalised.startswith(f"{key}-"):
            continue
        suffix = normalised[len(key) + 1 :]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", suffix):
            continue
        try:
            date.fromisoformat(suffix)
        except ValueError:
            continue
        matches.append(key)
    if matches:
        return PRICES[max(matches, key=len)]
    raise UnknownModelPrice(model)


def _weighted_length(text: str) -> int:
    return sum(
        1 if ord(character) <= 0x7F else 2 if ord(character) <= 0xFFFF else 4 for character in text
    )


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for completion usage, never zero."""
    return max(1, math.ceil(_weighted_length(text) / 2))


def estimate_prompt_tokens(text: str) -> int:
    """Conservative prompt estimate including request-format overhead."""
    return max(1, len(text.encode("utf-8")) + 512)


def cost_usd(
    price: ModelPrice,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    return (
        prompt_tokens * price.input_per_million + completion_tokens * price.output_per_million
    ) / 1_000_000
