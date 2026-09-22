# syntax=docker/dockerfile:1

FROM python:3.13-slim

ARG KABI_TG_CLI_VERSION=0.6.0

ENV DATA_DIR=/data \
    TG_SESSION_NAME=tg_cli \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN python -m pip install --no-cache-dir --disable-pip-version-check \
      "kabi-tg-cli==${KABI_TG_CLI_VERSION}" \
    && mkdir -p /data

COPY photo.py /opt/alicedev/tg-photo.py
COPY qr-login.py /opt/alicedev/tg-qr-login.py

RUN cp "$(command -v tg)" /usr/local/bin/tg-cli

COPY tg-wrapper.sh /usr/local/bin/tg

RUN chmod +x /usr/local/bin/tg /usr/local/bin/tg-cli

WORKDIR /data
VOLUME ["/data"]

# The container is an always-on execution target for tgctl. Commands are
# intentionally run with `docker exec ... tg ...`; the Telegram session and
# local cache live in the named /data volume.
CMD ["sleep", "infinity"]
