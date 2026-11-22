"""FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router
from .app import create_runtime
from .config import AppConfig, load_config

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # A countdown that was running when the server stopped continues, or fires, at once.
    app.state.runtime.orchestrator.tick_auto_advance()
    yield


def create_app(config: AppConfig | None = None) -> FastAPI:
    app = FastAPI(title="The AI Lion's Den", docs_url=None, redoc_url=None, lifespan=_lifespan)
    app.state.runtime = create_runtime(config)
    app.include_router(router)

    output = ROOT / "out"
    if output.is_dir():
        app.mount("/", StaticFiles(directory=output, html=True), name="ui")
    else:

        @app.get("/")
        async def missing_ui() -> dict[str, str]:
            return {"error": "The user interface is not built. Run `npm ci && npm run build`."}

    return app


def _load_environment() -> None:
    load_dotenv(ROOT / ".env")
    # Next.js-style local overrides take precedence when present.
    load_dotenv(ROOT / ".env.local", override=True)


_load_environment()

app = create_app(load_config({**os.environ, "NODE_ENV": "production"}))


def run() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "3000"))
    # App Service terminates HTTPS at its proxy and forwards the external scheme
    # in X-Forwarded-Proto. Trust those headers so links and cookies stay correct.
    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"),
    )


if __name__ == "__main__":
    run()
