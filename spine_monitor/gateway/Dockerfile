# syntax=docker/dockerfile:1
# Build context: repo root (so the root .dockerignore applies).
#   docker build -f spine_monitor/gateway/Dockerfile -t spine-gateway .

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
    uv sync --frozen --no-dev --package spine-gateway --no-install-workspace

# Project layer: source of the real gateway and the workspace packages it depends on
# (contracts brings the JSON Schemas, which the wheel bundles for validation)
COPY spine_monitor/contracts contracts
COPY spine_monitor/gateway gateway

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package spine-gateway --no-editable


# ---------- Stage 2: runtime (no uv, no sources, non-root) ----------
FROM python:3.14-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

CMD ["spine-gateway"]
