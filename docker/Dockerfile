FROM node:24-bookworm-slim

# Pin the public CLI release; every deployment verifies its schema and isolation.
ARG CODEX_VERSION=0.164.0
RUN npm install --global "@openai/codex@${CODEX_VERSION}" \
    && command -v timeout \
    && groupadd --gid 10001 character \
    && useradd --uid 10001 --gid 10001 --create-home character \
    && mkdir -p /home/character/.codex /home/character/.codex-dev /workspace/character /workspace/development /opt/character-seed \
    && chown -R 10001:10001 /home/character /workspace

# Node includes its own CA roots, but the native Codex login client needs the
# system trust bundle. The slim base image does not include it.
RUN apt-get update -o Acquire::Retries=2 -o Acquire::http::Timeout=30 \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY docker/skills /opt/character-seed/skills
COPY docker/bootstrap.mjs /opt/character-seed/bootstrap.mjs
COPY docker/bootstrap-protocol.mjs /opt/character-seed/bootstrap-protocol.mjs
USER 10001:10001
ENV HOME=/home/character
WORKDIR /workspace/character
ENTRYPOINT ["node", "/opt/character-seed/bootstrap.mjs"]
