"""Application wiring."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig, load_config
from .jury.orchestrator import JuryOrchestrator
from .jury.provider import create_jury_provider
from .jury.types import JuryProvider
from .store import Store


@dataclass
class Runtime:
    config: AppConfig
    store: Store
    provider: JuryProvider
    orchestrator: JuryOrchestrator


def create_runtime(config: AppConfig | None = None) -> Runtime:
    settings = config or load_config()
    store = Store(settings.db_path)
    provider = create_jury_provider(settings)
    orchestrator = JuryOrchestrator(store, provider, settings)
    return Runtime(
        config=settings,
        store=store,
        provider=provider,
        orchestrator=orchestrator,
    )
