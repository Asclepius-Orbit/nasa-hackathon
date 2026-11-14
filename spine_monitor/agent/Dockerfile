# syntax=docker/dockerfile:1
# Chat HTTP+SSE server of the LLM agent (contracts/agent-openapi.yaml). It starts the MCP server as a subprocess.
# Build context: repo root (so the root .dockerignore applies).
#   docker build -f spine_monitor/agent/Dockerfile -t spine-agent .

# ---------- Stage 1: builder (uv + build the virtualenv) ----------
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies layer: only the lockfile and the workspace manifests.
COPY spine_monitor/pyproject.toml spine_monitor/uv.lock ./
COPY spine_monitor/contracts/pyproject.toml contracts/
COPY spine_monitor/physiology/pyproject.toml physiology/
COPY spine_monitor/engine/pyproject.toml engine/
COPY spine_monitor/mcp_server/pyproject.toml mcp_server/
COPY spine_monitor/agent/pyproject.toml agent/
COPY spine_monitor/backend/pyproject.toml backend/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package spine-agent --no-install-workspace

# Project layer: the agent and the workspace packages it depends on
COPY spine_monitor/contracts contracts
COPY spine_monitor/mcp_server mcp_server
COPY spine_monitor/agent agent

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package spine-agent --no-editable


# ---------- Stage 2: runtime (no uv, no sources, non-root) ----------
FROM python:3.14-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --no-create-home app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# The token file is generated in the volume when SPINE_AGENT_HTTP_TOKEN is empty.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SPINE_AGENT_HTTP_HOST=0.0.0.0 \
    SPINE_AGENT_HTTP_PORT=8003 \
    SPINE_AGENT_HTTP_TOKEN_FILE=/data/agent-http-token
VOLUME ["/data"]

USER app
EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health', timeout=2)"]

CMD ["spine-agent-http"]
