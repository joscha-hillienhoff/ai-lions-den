"""OpenAI / Azure OpenAI jury provider."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from ..config import AppConfig
from .pricing import (
    MAX_JURY_COMPLETION_TOKENS,
    MAX_JURY_PROMPT_TOKENS,
    estimate_prompt_tokens,
)
from .prompts import (
    round1_system_prompt,
    round1_user_prompt,
    verdict_system_prompt,
    verdict_user_prompt,
)
from .types import Round1Input, Round1Output, VerdictInput, VerdictOutput
from .validation import (
    ROUND1_JSON_SCHEMA,
    VERDICT_JSON_SCHEMA,
    parse_json_content,
    parse_round1_output,
    parse_verdict_output,
)

logger = logging.getLogger(__name__)

_RETRYABLE_MESSAGE = re.compile(
    r"fetch failed|network|socket hang up|ECONNRESET|ETIMEDOUT", re.IGNORECASE
)


def _status(error: Any) -> int | None:
    for attribute in ("status_code", "status"):
        value = getattr(error, attribute, None)
        if isinstance(value, int):
            return value
    return None


def _message(error: Any) -> str:
    return str(getattr(error, "message", None) or error)


def _is_retryable(error: Any) -> bool:
    status = _status(error)
    if status == 429 or (status is not None and status >= 500):
        return True
    name = type(error).__name__
    if name in {"APIConnectionError", "APITimeoutError", "APIConnectionTimeoutError"}:
        return True
    return bool(_RETRYABLE_MESSAGE.search(_message(error)))


def _is_temperature_rejection(error: Any) -> bool:
    status = _status(error)
    if status not in (400, 422):
        return False
    return "temperature" in _message(error).lower()


class OpenAiJuryProvider:
    name = "openai"

    def __init__(
        self,
        config: AppConfig,
        *,
        on_usage: Callable[[int | None, int | None], None] | None = None,
        before_retry: Callable[[], None] | None = None,
        on_preflight_failure: Callable[[], None] | None = None,
        benchmark_limits: bool = False,
    ) -> None:
        if not config.api_key:
            raise ValueError("OpenAI provider requires an API key.")
        # The SDK falls back to its OPENAI_BASE_URL environment variable when the argument
        # is None, and an empty value there becomes an empty base URL. Always pass the
        # resolved address so an unset or empty configuration means the public API.
        base_url = config.base_url or "https://api.openai.com/v1"
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=f"{base_url}/",
            timeout=config.request_timeout_ms / 1000,
            max_retries=0,
        )
        self._model = config.model
        self._temperature = config.temperature
        self._temperature_unsupported = False
        self._on_usage = on_usage
        self._before_retry = before_retry
        self._on_preflight_failure = on_preflight_failure
        self._benchmark_limits = benchmark_limits

    async def evaluate_round1(self, input_value: Round1Input) -> Round1Output:
        raw = await self._complete(
            schema_name="jury_round1",
            schema=ROUND1_JSON_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": round1_system_prompt(input_value.investor),
                },
                {
                    "role": "user",
                    "content": round1_user_prompt(
                        team_name=input_value.team_name, pitch=input_value.pitch
                    ),
                },
            ],
        )
        return parse_round1_output(raw)

    async def evaluate_verdict(self, input_value: VerdictInput) -> VerdictOutput:
        raw = await self._complete(
            schema_name="jury_verdict",
            schema=VERDICT_JSON_SCHEMA,
            messages=[
                {
                    "role": "system",
                    "content": verdict_system_prompt(input_value.investor),
                },
                {"role": "user", "content": verdict_user_prompt(input_value)},
            ],
        )
        return parse_verdict_output(raw)

    async def _complete(self, *, schema_name: str, schema: dict, messages: list[dict]) -> Any:
        max_attempts = 3
        last_error: Exception | None = None
        attempt = 0

        while attempt < max_attempts:
            send_temperature = self._temperature is not None and not self._temperature_unsupported
            try:
                if self._benchmark_limits:
                    prompt_tokens = estimate_prompt_tokens(
                        "\n".join(str(message.get("content", "")) for message in messages)
                        + json.dumps(schema, separators=(",", ":"))
                    )
                    if prompt_tokens > MAX_JURY_PROMPT_TOKENS:
                        if self._on_preflight_failure is not None:
                            self._on_preflight_failure()
                        raise RuntimeError("The jury prompt exceeds the benchmark token limit.")

                arguments: dict[str, Any] = {
                    "model": self._model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": schema,
                        },
                    },
                }
                if self._benchmark_limits:
                    arguments["max_tokens"] = MAX_JURY_COMPLETION_TOKENS
                if send_temperature:
                    arguments["temperature"] = self._temperature

                completion = await self._client.chat.completions.create(**arguments)
                if self._on_usage is not None:
                    usage = getattr(completion, "usage", None)
                    prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
                    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
                    self._on_usage(prompt_tokens, completion_tokens)
                choice = completion.choices[0] if completion.choices else None
                if choice is None:
                    raise RuntimeError("The model returned no choices.")
                if choice.message.refusal:
                    raise RuntimeError(f"The model refused the request: {choice.message.refusal}")
                return parse_json_content(choice.message.content)
            except Exception as error:  # noqa: BLE001 - provider errors are opaque
                last_error = error

                # Decide from this request, not from the shared flag: concurrent calls
                # may have already set it after sending their own temperature. The retry
                # omits temperature and does not consume a transient-error attempt; a
                # second rejection cannot loop because that retry sends no temperature.
                if send_temperature and _is_temperature_rejection(error):
                    if not self._temperature_unsupported:
                        logger.warning(
                            "[jury] model %s rejected temperature; retrying without it "
                            "for the rest of the session.",
                            self._model,
                        )
                        self._temperature_unsupported = True
                    if self._before_retry is not None:
                        self._before_retry()
                    continue

                if attempt < max_attempts - 1 and _is_retryable(error):
                    import random

                    backoff = 1_000 * 2**attempt + int(random.random() * 400)
                    logger.warning(
                        "[jury] transient model error, retrying in %dms: %s",
                        backoff,
                        _message(error),
                    )
                    await asyncio.sleep(backoff / 1000)
                    if self._before_retry is not None:
                        self._before_retry()
                    attempt += 1
                    continue

                break

        if last_error is not None:
            raise last_error
        raise RuntimeError("The model request failed.")
