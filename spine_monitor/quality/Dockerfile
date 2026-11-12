# syntax=docker/dockerfile:1
# Quality gate image. Build context: repo root.
#   docker build -f spine_monitor/quality/Dockerfile -t spine-quality .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

# UV_PYTHON pins uv to the image interpreter, whatever spine_monitor/.python-version says.
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PYTHON=/usr/local/bin/python \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends git libatomic1 && rm -rf /var/lib/apt/lists/*

# The workspace lives at the same path the compose file bind-mounts, so editable installs resolve.
WORKDIR /work/spine_monitor

COPY spine_monitor/pyproject.toml spine_monitor/uv.lock ./
COPY spine_monitor/contracts/pyproject.toml contracts/
COPY spine_monitor/physiology/pyproject.toml physiology/
COPY spine_monitor/engine/pyproject.toml engine/
COPY spine_monitor/mcp_server/pyproject.toml mcp_server/
COPY spine_monitor/agent/pyproject.toml agent/
COPY spine_monitor/backend/pyproject.toml backend/
COPY spine_monitor/gateway/pyproject.toml gateway/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-install-workspace

COPY spine_monitor /work/spine_monitor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages

# Warm pyright's node runtime so the gate does not need the network at run time.
RUN pyright --version

CMD ["sh", "quality/gate.sh"]
