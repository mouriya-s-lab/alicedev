# syntax=docker/dockerfile:1
#
# AstrBot for alicedev: the pinned upstream image plus a static docker CLI so
# the plugin can reach the paseo daemon through its only contact surface, the
# paseo CLI, via `docker exec alicedev-paseo paseo … --json` (ARCHITECTURE §4).
# The Docker socket is mounted by Compose. The plugin (bot/) and DSL
# (templates/) are bind-mounted from the deployctl-owned checkout, never baked.

ARG ASTRBOT_IMAGE=soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf
FROM ${ASTRBOT_IMAGE}

ARG DOCKER_CLI_VERSION=29.8.1

USER root
RUN set -eux; \
    python3 -c "import urllib.request, tarfile, io; \
data = urllib.request.urlopen('https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_CLI_VERSION}.tgz', timeout=120).read(); \
t = tarfile.open(fileobj=io.BytesIO(data)); \
m = t.getmember('docker/docker'); m.name = 'docker'; \
t.extract(m, '/usr/local/bin')"; \
    chmod 0755 /usr/local/bin/docker; \
    docker --version
