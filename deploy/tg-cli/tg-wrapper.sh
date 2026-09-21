#!/bin/sh
set -eu

if [ "${1:-}" = "photo" ]; then
    exec python /opt/alicedev/tg-photo.py "$@"
fi

exec /usr/local/bin/tg-cli "$@"
