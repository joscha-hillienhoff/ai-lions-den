from __future__ import annotations

from ..config import AppConfig
from .demo import DemoJuryProvider
from .openai import OpenAiJuryProvider
from .types import JuryProvider


def create_jury_provider(config: AppConfig) -> JuryProvider:
    if config.demo or not config.api_key:
        return DemoJuryProvider(config.demo_delay_ms)
    return OpenAiJuryProvider(config)
