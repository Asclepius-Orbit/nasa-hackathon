# Quality gate of app/ui (lint, types, contract types, tests+coverage, audit). Build context: the repo root;
# the repo is bind-mounted at /work at run time (check:types reads spine_monitor/contracts/schemas/v1).
FROM node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    CI=true
RUN corepack enable

# node_modules is a named volume: pre-create it owned by the non-root user so the volume inherits that ownership.
RUN mkdir -p /work/app/ui/node_modules && chown -R node:node /work
USER node
WORKDIR /work/app/ui
