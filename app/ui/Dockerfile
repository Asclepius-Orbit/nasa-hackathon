# syntax=docker/dockerfile:1
# The desktop UI (Electron) in a container, shown in the browser through noVNC.
# Electron is a desktop app: the renderer only talks to the main process over IPC (window.asclepius), so it cannot be
# served as a static site. The container runs the real app on a virtual display (Xvfb) and exposes it at
# http://localhost:6080/vnc.html.
# Build context: app/ui.   docker build -t spine-ui app/ui

# ---------- Stage 1: build (electron-vite) ----------
FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=true
RUN corepack enable
WORKDIR /build

# Dependencies layer: rebuilt only when the lockfile changes. The Electron binary is downloaded here by postinstall.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY scripts scripts
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY . .
# "production" is the real build; "e2e-real" also seeds the demo crew.
ARG BUILD_MODE=production
RUN pnpm exec electron-vite build --mode "$BUILD_MODE"

# node_modules is kept whole: electron (the runtime binary) is a devDependency of this package.


# ---------- Stage 2: runtime (Electron + virtual display + noVNC) ----------
FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb x11vnc novnc websockify socat \
        libgtk-3-0 libnss3 libasound2 libgbm1 libxss1 libxtst6 libxkbcommon0 libdrm2 libatk-bridge2.0-0 libcups2 \
        fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder --chown=node:node /build /app
COPY --chown=node:node docker/entrypoint.sh /usr/local/bin/spine-ui-entrypoint
RUN chmod +x /usr/local/bin/spine-ui-entrypoint \
    && mkdir -p /home/node/.config && chown -R node:node /home/node

USER node
WORKDIR /app
ENV HOME=/home/node \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1440x900x24 \
    NOVNC_PORT=6080
# Crew, PIN state and settings (Electron userData) live in a volume.
VOLUME ["/home/node/.config"]
EXPOSE 6080

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.NOVNC_PORT || 6080) + '/vnc.html').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]

ENTRYPOINT ["spine-ui-entrypoint"]
