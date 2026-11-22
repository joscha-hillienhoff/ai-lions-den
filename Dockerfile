# syntax=docker/dockerfile:1

# Build the static user interface first so the runtime image never needs Node.js.
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# Runtime: Python 3.12, its locked production dependencies and the prebuilt UI.
FROM python:3.12-slim AS runtime

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PORT=3000

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY backend ./backend
COPY --from=ui /ui/out ./out

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /home/data \
    && chown -R app:app /app /home/data
USER app

EXPOSE 3000
CMD ["python", "-m", "backend.main"]
