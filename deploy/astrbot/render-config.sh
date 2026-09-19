#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

render_one() {
    local source=$1
    local destination=$2
    mkdir -p "$(dirname -- "$destination")"
    python3 - "$source" "$destination" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
text = source.read_text(encoding="utf-8")
pattern = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
full_value = re.compile(r'"\$\{([A-Z][A-Z0-9_]*)\}"')
required = {
    "ALICEDEV_HOST",
    "TELEGRAM_BOT_TOKEN",
    "PASEO_PASSWORD",
    "ALICEDEV_INTERNAL_TOKEN",
    "NAPCAT_ONEBOT_TOKEN",
}
found = set(pattern.findall(text))
missing = sorted(name for name in required & found if not os.environ.get(name))
if missing:
    raise SystemExit("missing required environment variables: " + ", ".join(missing))


def value(name: str) -> str:
    return os.environ.get(name, "")


def replace_full(match: re.Match[str]) -> str:
    return json.dumps(value(match.group(1)), ensure_ascii=False)


def replace_embedded(match: re.Match[str]) -> str:
    encoded = json.dumps(value(match.group(1)), ensure_ascii=False)
    return encoded[1:-1]

rendered = full_value.sub(replace_full, text)
rendered = pattern.sub(replace_embedded, rendered)
if pattern.search(rendered):
    raise SystemExit(f"unresolved placeholder in {source}")
try:
    payload = json.loads(rendered)
except json.JSONDecodeError as exc:
    raise SystemExit(f"invalid rendered JSON in {source}: {exc}") from exc
if isinstance(payload, dict) and isinstance(payload.get("admin_users"), str):
    raw_admins = payload["admin_users"]
    payload["admin_users"] = [part for part in (s.strip() for s in raw_admins.split(",")) if part]
tmp = destination.with_name(destination.name + ".tmp")
tmp.write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
os.chmod(tmp, 0o600)
os.replace(tmp, destination)
PY
}

if [[ $# -eq 0 ]]; then
    render_one "$script_dir/cmd_config.json" "$script_dir/cmd_config.rendered.json"
    render_one "$script_dir/alicedev_config.json" "$script_dir/alicedev_config.rendered.json"
elif [[ $# -eq 2 ]]; then
    render_one "$1" "$2"
else
    echo "usage: $0 [TEMPLATE OUTPUT]" >&2
    exit 2
fi
