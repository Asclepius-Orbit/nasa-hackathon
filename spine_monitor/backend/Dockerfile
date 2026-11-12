# syntax=docker/dockerfile:1
# Build context: repo root (so the root .dockerignore applies).
#   docker build -f spine_monitor/backend/Dockerfile -t spine-backend .

# ---------- Stage 1: builder (uv + build the virtualenv) ----------
FROM python:3.14-slim AS builder

# uv binary, pinned to the version used locally
COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

# Precompile .pyc, copy (not hardlink) from the cache mount, never download another Python
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies layer: only the lockfile and the workspace manifests,
# so this layer is rebuilt only when dependencies change, not on code edits.
COPY spine_monitor/pyproject.toml spine_monitor/uv.lock ./
COPY spine_monitor/contracts/pyproject.toml contracts/
COPY spine_monitor/physiology/pyproject.toml physiology/
COPY spine_monitor/engine/pyproject.toml engine/
COPY spine_monitor/mcp_server/pyproject.toml mcp_server/
COPY spine_monitor/agent/pyproject.toml agent/
COPY spine_monitor/backend/pyproject.toml backend/
COPY spine_monitor/gateway/pyproject.toml gateway/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package spine-backend --no-install-workspace

# Project layer: source of the backend and the workspace packages it depends on
COPY spine_monitor/contracts contracts
COPY spine_monitor/backend backend

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package spine-backend --no-editable


# ---------- Stage 2: runtime (no uv, no sources, non-root) ----------
FROM python:3.14-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# SQLite with the app state lives in a volume
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SPINE_APP_DB_PATH=/data/spine_monitor.db
VOLUME ["/data"]

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]

CMD ["uvicorn", "--factory", "monitor.main:create_app", "--host", "0.0.0.0", "--port", "8000"]
