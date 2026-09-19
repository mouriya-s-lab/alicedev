# syntax=docker/dockerfile:1
# The Compose paseo-base service builds this image from
# ../../../paseo-alicedev/docker/arch (relative to this Dockerfile directory).
ARG PASEO_ARCH_IMAGE=alicedev/paseo-arch:local
FROM ${PASEO_ARCH_IMAGE}

USER root

# These are produced by `make harness` in the alicedev build context.
COPY harness/dist/omp-extension/ /opt/alicedev/omp-extension/
COPY harness/dist/reply-cli/alicedev-reply /usr/local/bin/alicedev-reply

COPY deploy/paseo/entrypoint-hook.sh /usr/local/bin/alicedev-paseo-entrypoint

RUN set -eux; \
    chmod +x /usr/local/bin/alicedev-reply /usr/local/bin/alicedev-paseo-entrypoint; \
    mkdir -p /opt/alicedev; \
    cat > /opt/alicedev/paseo-config.json <<'JSON'
{
  "$schema": "https://paseo.sh/schemas/paseo.config.v1.json",
  "version": 1,
  "daemon": {
    "listen": "0.0.0.0:6767",
    "hostnames": ["localhost"],
    "mcp": {
      "enabled": true
    },
    "relay": {
      "enabled": false
    }
  },
  "agents": {
    "providers": {
      "omp": {
        "enabled": true
      },
      "omp-alicedev": {
        "enabled": true,
        "extends": "omp",
        "label": "omp + alicedev",
        "command": ["omp", "-e", "/opt/alicedev/omp-extension"]
      }
    }
  }
}
JSON

ENTRYPOINT ["/usr/local/bin/alicedev-paseo-entrypoint"]
