#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
repo_url=${ALICEDEV_REPO_URL:-https://github.com/mouriya-s-lab/alicedev.git}
workspace=/workspace/alicedev
container=alicedev-paseo

usage() {
  cat >&2 <<'EOF'
usage: bootstrap-fixed-main.sh [--workspace <path>] [--repo-url <url>] [--container <name> | --local]

Create (or validate) the alignment-only fixed-main checkout of alicedev inside
the paseo /workspace volume, then align it with mainsync.

The fixed-main checkout is never an AI working directory: sessions only run in
worktrees derived from it (paseo workspace create --isolation worktree). Only
mainsync may mutate it after bootstrap.

Default: runs git as the paseo user inside the alicedev-paseo container, whose
git credential helper reads GITHUB_TOKEN. --local runs git directly (tests).
EOF
}

fail_bootstrap() {
  printf 'fixed_main_bootstrap_failed: %s\n' "$1" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --workspace) [[ $# -ge 2 ]] || { usage; exit 2; }; workspace=$2; shift 2 ;;
    --repo-url) [[ $# -ge 2 ]] || { usage; exit 2; }; repo_url=$2; shift 2 ;;
    --container) [[ $# -ge 2 ]] || { usage; exit 2; }; container=$2; shift 2 ;;
    --local) container=; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ -n $repo_url ]] || fail_bootstrap 'repository URL is empty'

x() {
  if [[ -n $container ]]; then
    docker exec -i --user paseo "$container" "$@"
  else
    "$@"
  fi
}

# Clone only into an absent or empty directory; an existing checkout is
# validated, never reset, cleaned, stashed or rebased.
if x test -d "$workspace/.git"; then
  :
elif x test -e "$workspace" && [[ -n $(x find "$workspace" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  fail_bootstrap "workspace exists, is not empty and is not a git checkout: $workspace"
else
  x git clone --quiet --branch main --single-branch "$repo_url" "$workspace" >/dev/null 2>&1 ||
    fail_bootstrap "unable to clone main from $repo_url"
fi

origin_url=$(x git -C "$workspace" remote get-url origin 2>/dev/null || true)
[[ $origin_url == "$repo_url" ]] || fail_bootstrap "workspace origin ($origin_url) does not match --repo-url"
[[ $(x git -C "$workspace" symbolic-ref --quiet --short HEAD 2>/dev/null || true) == main ]] ||
  fail_bootstrap 'workspace must be on local main'
[[ $(x git -C "$workspace" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true) == origin/main ]] ||
  fail_bootstrap 'local main must track origin/main'

if [[ -n $container ]]; then
  "$script_dir/mainsync" align --repo "$workspace" --container "$container"
else
  "$script_dir/mainsync" align --repo "$workspace"
fi
printf 'fixed-main ready: %s\n' "$(x git -C "$workspace" rev-parse HEAD)"
