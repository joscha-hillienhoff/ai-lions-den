"""Token accounting and the hard budget guard for the benchmark."""

from __future__ import annotations

import contextvars
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from ..jury.pricing import (
    MAX_JURY_COMPLETION_TOKENS,
    MAX_JURY_PROMPT_TOKENS,
    ModelPrice,
    cost_usd,
)

# Conservative worst case for one call. The guard reserves this before every
# request so the benchmark cannot start a call that would cross the cap.
RESERVE_PROMPT_TOKENS = MAX_JURY_PROMPT_TOKENS
RESERVE_COMPLETION_TOKENS = MAX_JURY_COMPLETION_TOKENS


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class SpendSnapshot:
    usd: float
    prompt_tokens: int
    completion_tokens: int
    calls: int

    def as_dict(self) -> dict[str, float | int]:
        return {
            "usd": round(self.usd, 6),
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "calls": self.calls,
        }


class UsageMeter:
    """Accumulates real usage and refuses calls without budget headroom."""

    def __init__(
        self,
        *,
        budget_usd: float,
        price: ModelPrice,
        reserve_prompt_tokens: int = RESERVE_PROMPT_TOKENS,
        reserve_completion_tokens: int = RESERVE_COMPLETION_TOKENS,
    ) -> None:
        if not math.isfinite(budget_usd) or budget_usd <= 0 or budget_usd >= 5:
            raise ValueError("budget_usd must be finite, positive, and under 5")
        self.budget_usd = budget_usd
        self.price = price
        self.reserve_prompt_tokens = reserve_prompt_tokens
        self.reserve_completion_tokens = reserve_completion_tokens
        self.reserve_usd = cost_usd(price, reserve_prompt_tokens, reserve_completion_tokens)
        self._spent = 0.0
        self._prompt = 0
        self._completion = 0
        self._calls = 0
        self._reserved_usd = 0.0
        self._reserved_prompt = 0
        self._reserved_completion = 0
        self._reservations: dict[int, None] = {}
        self._next_reservation = 0
        self._current_reservation: contextvars.ContextVar[int | None] = contextvars.ContextVar(
            "benchmark_reservation", default=None
        )
        self.abort_reason: str | None = None

    @property
    def spent_usd(self) -> float:
        return self._spent + self._reserved_usd

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def aborted(self) -> bool:
        return self.abort_reason is not None

    def before_call(self) -> int:
        if self.aborted:
            raise BudgetExceeded(self.abort_reason or "The budget is exhausted.")
        if self._spent + self._reserved_usd + self.reserve_usd > self.budget_usd:
            self.abort_reason = (
                f"Budget of {self.budget_usd:.2f} USD would be crossed: "
                f"spent {self.spent_usd:.4f} USD plus a {self.reserve_usd:.4f} USD "
                "reserve for the next call exceeds the cap."
            )
            raise BudgetExceeded(self.abort_reason)
        self._next_reservation += 1
        reservation = self._next_reservation
        self._reserved_usd += self.reserve_usd
        self._reserved_prompt += self.reserve_prompt_tokens
        self._reserved_completion += self.reserve_completion_tokens
        self._reservations[reservation] = None
        self._calls += 1
        return reservation

    @contextmanager
    def reservation_scope(self, reservation: int) -> Iterator[None]:
        marker = self._current_reservation.set(reservation)
        try:
            yield
        finally:
            self._current_reservation.reset(marker)

    def _reservation_for(self, reservation: int | None) -> int | None:
        if reservation is not None:
            return reservation
        current = self._current_reservation.get()
        if current is not None:
            return current
        if len(self._reservations) == 1:
            return next(iter(self._reservations))
        if self._reservations:
            return next(iter(self._reservations))
        return None

    def _remove_reservation(self, reservation: int) -> bool:
        if reservation not in self._reservations:
            return False
        del self._reservations[reservation]
        self._reserved_usd -= self.reserve_usd
        self._reserved_prompt -= self.reserve_prompt_tokens
        self._reserved_completion -= self.reserve_completion_tokens
        return True

    def release(self, reservation: int | None = None) -> None:
        token = self._reservation_for(reservation)
        if token is None or not self._remove_reservation(token):
            return
        self._calls -= 1

    def charge_reservation(self, reservation: int | None = None) -> None:
        token = self._reservation_for(reservation)
        if token is None or not self._remove_reservation(token):
            return
        self._spent += self.reserve_usd
        self._prompt += self.reserve_prompt_tokens
        self._completion += self.reserve_completion_tokens

    def retry(self) -> None:
        self.charge_reservation()
        reservation = self.before_call()
        self._current_reservation.set(reservation)

    def record(
        self,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        reservation: int | None = None,
    ) -> None:
        token = self._reservation_for(reservation)
        if token is None:
            token = self.before_call()
        elif token not in self._reservations:
            if self._reservations:
                return
            token = self.before_call()
        self._remove_reservation(token)

        if prompt_tokens is None or completion_tokens is None:
            prompt = self.reserve_prompt_tokens
            completion = self.reserve_completion_tokens
            charge = self.reserve_usd
        else:
            prompt = max(0, int(prompt_tokens))
            completion = max(0, int(completion_tokens))
            charge = cost_usd(self.price, prompt, completion)
        self._prompt += prompt
        self._completion += completion
        self._spent += charge

    def snapshot(self) -> SpendSnapshot:
        return SpendSnapshot(
            usd=self.spent_usd,
            prompt_tokens=self._prompt + self._reserved_prompt,
            completion_tokens=self._completion + self._reserved_completion,
            calls=self._calls,
        )
