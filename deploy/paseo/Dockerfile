# syntax=docker/dockerfile:1
#
# alicedev runtime layer on top of an unmodified upstream paseo image
# (ARCHITECTURE §11). The base is built by the Compose `paseo-base` service from
# mouriya-s-lab/paseo source with its own docker/base/Dockerfile and
# EXPO_PUBLIC_PASEO_SELFHOSTED=true — no paseo source is patched.
#
# Build context: the alicedev checkout root (host /srv/alicedev/app).
# This layer only adds: the omp harness (bun), git/gh, a static docker CLI,
# agent-browser + chromium (e2e screenshots), the alicedev harness artifacts,
# the daemon provider config and a git credential helper reading GITHUB_TOKEN.

ARG PASEO_BASE_IMAGE=alicedev/paseo-base:local
ARG NODE_IMAGE=node:22-bookworm-slim

FROM ${NODE_IMAGE} AS harness
WORKDIR /src/harness
COPY harness/package.json harness/package-lock.json ./
RUN npm ci
COPY harness/ ./
RUN npm run build && test -f dist/omp-extension/index.js && test -x dist/reply-cli/alicedev-reply

FROM ${PASEO_BASE_IMAGE}

ARG OMP_VERSION=18.2.5
ARG BUN_VERSION=1.4.2
ARG DOCKER_CLI_VERSION=29.8.1
ARG AGENT_BROWSER_VERSION=0.38.1
# gid of the host `docker` group (owner of /var/run/docker.sock). The base
# entrypoint drops to `paseo` with gosu, which re-derives supplementary groups
# from /etc/group, so membership must exist in the image (group_add is lost).
ARG DOCKER_GID=989

USER root
ENV BUN_INSTALL=/opt/bun \
    AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium \
    AGENT_BROWSER_ARGS=--no-sandbox,--disable-dev-shm-usage

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl git gnupg unzip python3 chromium fonts-noto-cjk; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends gh; \
    rm -rf /var/lib/apt/lists/*; \
    curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz" \
      | tar -xz -C /usr/local/bin --strip-components=1 docker/docker; \
    docker --version; \
    npm install -g "bun@${BUN_VERSION}" "agent-browser@${AGENT_BROWSER_VERSION}"; \
    mkdir -p "${BUN_INSTALL}"; \
    bun install --global "@oh-my-pi/pi-coding-agent@${OMP_VERSION}"; \
    ln -sf "${BUN_INSTALL}/bin/omp" /usr/local/bin/omp; \
    omp --version; \
    agent-browser --version; \
    chmod -R a+rX "${BUN_INSTALL}"; \
    npm cache clean --force

# Git identity for agent commits and a credential helper that reads
# GITHUB_TOKEN at call time (the token is never written to disk).
RUN set -eux; \
    git config --system user.name "RiriAgent"; \
    git config --system user.email "39219425+RiriAgent@users.noreply.github.com"; \
    git config --system credential.https://github.com.helper \
      '!f() { test "$1" = get || exit 0; echo username=x-access-token; echo "password=${GITHUB_TOKEN}"; }; f'; \
    git config --system --add safe.directory '*'

# Python toolchain for agents working on the alicedev repo: `python -m alicedev.dsl
# check|card` and `pytest bot/tests` need the bot's dependencies. The venv leads
# PATH for plain and login shells (/etc/profile resets PATH, hence profile.d).
COPY bot/requirements.txt /tmp/alicedev-requirements.txt
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3-venv python3-pip; \
    python3 -m venv /opt/alicedev/pyenv; \
    /opt/alicedev/pyenv/bin/pip install --no-cache-dir -r /tmp/alicedev-requirements.txt pytest python-frontmatter; \
    ln -sf /opt/alicedev/pyenv/bin/pytest /usr/local/bin/pytest; \
    echo 'PATH=/opt/alicedev/pyenv/bin:$PATH; export PATH' > /etc/profile.d/alicedev-pyenv.sh; \
    rm -f /tmp/alicedev-requirements.txt; \
    rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/alicedev/pyenv/bin:$PATH

COPY --from=harness /src/harness/dist/omp-extension/ /opt/alicedev/omp-extension/
COPY --from=harness /src/harness/dist/reply-cli/alicedev-reply /opt/alicedev/reply-cli/alicedev-reply
COPY deploy/paseo/entrypoint-hook.sh /usr/local/bin/alicedev-paseo-entrypoint
COPY deploy/paseo/paseo-config.json /opt/alicedev/paseo-config.json

RUN set -eux; \
    chmod +x /opt/alicedev/reply-cli/alicedev-reply /usr/local/bin/alicedev-paseo-entrypoint; \
    ln -sf /opt/alicedev/reply-cli/alicedev-reply /usr/local/bin/alicedev-reply; \
    existing="$(getent group "${DOCKER_GID}" | cut -d: -f1 || true)"; \
    if [ -z "${existing}" ]; then groupadd --gid "${DOCKER_GID}" docker-host; existing=docker-host; fi; \
    usermod -aG "${existing}" paseo; \
    mkdir -p /deploy/app /deploy/e2e-src; \
    chown paseo:paseo /deploy/app /deploy/e2e-src

ENTRYPOINT ["/usr/local/bin/alicedev-paseo-entrypoint"]
