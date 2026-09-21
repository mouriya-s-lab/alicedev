#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
default_repo_url=${ALICEDEV_REPO_URL:-https://github.com/mouriya-s-lab/alicedev.git}
repo_url=$default_repo_url
workspace=
run_id=

usage() {
  cat >&2 <<'EOF'
usage: bootstrap-fixed-main.sh --workspace <path> [--repo-url <url>] [--run <id>]

Create (or validate) an alignment-only alicedev checkout pinned to main, align
it with mainsync, and optionally derive one run worktree with worktreectl.

The fixed-main checkout is never an AI/conductor target. Only mainsync align
may operate on it after bootstrap; use the derived worktree for every run.
EOF
}

fail_bootstrap() {
  printf 'fixed_main_bootstrap_failed: %s\n' "$1" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --workspace)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      workspace=$2
      shift 2
      ;;
    --repo-url)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      repo_url=$2
      shift 2
      ;;
    --run)
      [[ $# -ge 2 ]] || { usage; exit 2; }
      run_id=$2
      shift 2
      ;;
    -h|--help)
      usage >&1
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

[[ -n $workspace ]] || { usage; exit 2; }
[[ -n $repo_url ]] || fail_bootstrap 'repository URL is empty'
if [[ -n $run_id && $run_id == -* ]]; then
  fail_bootstrap 'run id must not begin with -'
fi

# Resolve the parent before cloning so a symlinked path cannot accidentally
# bypass the production guard. Never allow this helper to touch prod paseo
# state, even when a caller supplies it by mistake.
workspace_parent=$(dirname -- "$workspace")
workspace_name=$(basename -- "$workspace")
case $workspace in
  /srv/alicedev|/srv/alicedev/*)
    fail_bootstrap 'production /srv/alicedev paths are forbidden'
    ;;
esac
mkdir -p "$workspace_parent"
workspace_parent=$(CDPATH= cd -- "$workspace_parent" && pwd -P)
workspace=$workspace_parent/$workspace_name
if [[ -e $workspace ]]; then
  workspace=$(CDPATH= cd -- "$workspace" && pwd -P)
fi
if [[ $workspace == /srv/alicedev || $workspace == /srv/alicedev/* ]]; then
  fail_bootstrap 'production /srv/alicedev paths are forbidden'
fi

if [[ -d $workspace && -z $(find "$workspace" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  # A paseo volume may pre-create an empty mountpoint. Cloning into that
  # directory is equivalent to cloning into an absent path and does not
  # overwrite any caller-owned content.
  if ! git clone --branch main --single-branch "$repo_url" "$workspace" >/dev/null 2>&1; then
    fail_bootstrap "unable to clone main from $repo_url"
  fi
elif [[ -e $workspace ]]; then
  if ! git -C "$workspace" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail_bootstrap "workspace exists but is not a Git worktree: $workspace"
  fi
else
  if ! git clone --branch main --single-branch "$repo_url" "$workspace" >/dev/null 2>&1; then
    fail_bootstrap "unable to clone main from $repo_url"
  fi
fi

# A bootstrap may only reuse the intended alicedev origin and the fixed local
# main branch. It never checks out, resets, cleans, stashes, or rebases an
# existing checkout.
origin_url=$(git -C "$workspace" remote get-url origin 2>/dev/null || true)
[[ -n $origin_url ]] || fail_bootstrap 'workspace has no origin remote'
if [[ $origin_url != "$repo_url" ]]; then
  fail_bootstrap "workspace origin does not match --repo-url"
fi
if [[ $(git -C "$workspace" symbolic-ref --quiet --short HEAD 2>/dev/null || true) != main ]]; then
  fail_bootstrap 'workspace must already be on local main'
fi
if [[ $(git -C "$workspace" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true) != origin/main ]]; then
  fail_bootstrap 'local main must track origin/main'
fi


# This is the sole mutation of fixed-main after clone. Keep its stable
# main_sync_failed output untouched so callers can emit the contract event.
"$script_dir/mainsync" align --repo "$workspace"
printf 'fixed-main ready: %s\n' "$(git -C "$workspace" rev-parse HEAD)"

if [[ -n $run_id ]]; then
  worktreectl_bin=${WORKTREECTL_BIN:-worktreectl}
  if [[ $worktreectl_bin == */* ]]; then
    [[ -x $worktreectl_bin ]] || fail_bootstrap "worktreectl is not executable: $worktreectl_bin"
  elif ! command -v "$worktreectl_bin" >/dev/null 2>&1; then
    fail_bootstrap 'worktreectl not found; set WORKTREECTL_BIN to paseo/tools/worktreectl'
  fi

  # Derivation is intentionally downstream of a successful alignment. The
  # fixed-main path is passed only as a source repository, never as an agent
  # or conductor cwd.
  "$worktreectl_bin" new --run "$run_id" --repo "$workspace"
fi
