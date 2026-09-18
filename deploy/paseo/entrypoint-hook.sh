#!/usr/bin/env bash
set -euo pipefail

: "${HOME:=/home/paseo}"
: "${PASEO_HOME:=${HOME}/.paseo}"
config_path="${PASEO_HOME}/config.json"

mkdir -p "${PASEO_HOME}"
if [[ ! -s "${config_path}" ]]; then
    install -m 0644 /opt/alicedev/paseo-config.json "${config_path}"
fi

if [[ "$(id -u)" == "0" ]]; then
    chown paseo:paseo "${config_path}" "${PASEO_HOME}"
fi

exec /usr/bin/tini -- /usr/local/bin/paseo-docker-entrypoint "$@"
