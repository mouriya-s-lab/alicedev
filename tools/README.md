# Fixed-main workspace

`bootstrap-fixed-main.sh` creates an **alignment-only** checkout of this
repository for the paseo daemon. The checkout is pinned to local `main`, which
tracks `origin/main`:

```sh
./tools/bootstrap-fixed-main.sh \
  --workspace /workspace/alicedev-fixed-main \
  --repo-url https://github.com/mouriya-s-lab/alicedev.git
```

The helper clones `main` when the path is absent, validates the origin and
branch when it already exists, and then runs exactly:

```sh
tools/mainsync align --repo /workspace/alicedev-fixed-main
```

`mainsync align` fetches `origin/main` and advances local `main` with
`git merge --ff-only`. A dirty tree, a local-ahead/diverged branch, a detached
HEAD, a missing `origin/main` tracking relationship, or any fetch/merge error
fails closed with the machine-visible reason `main_sync_failed`. It never
resets, cleans, stashes, rebases, or merges the fixed-main checkout.

## Non-negotiable invariant

The fixed-main path is **never an AI target**. No `create_agent`, conductor
session, or run session may use it as a cwd. After bootstrap, its only allowed
operation is `mainsync align`; all implementation, test, and e2e activity uses
a per-run worktree. The bootstrap helper refuses `/srv/alicedev` production
paths.

## Derive a run worktree

Derivation is gated on a successful alignment. Pass the fixed-main path as the
source repository to the paseo-owned `worktreectl`; it creates a distinct
worktree from the aligned local `main`. The run id deterministically names its
`upgrade/<run-id>` branch:

```sh
WORKTREECTL_BIN=/path/to/paseo-alicedev/tools/worktreectl \
  ./tools/bootstrap-fixed-main.sh \
    --workspace /workspace/alicedev-fixed-main \
    --run <run-id>

# Equivalent explicit sequence:
tools/mainsync align --repo /workspace/alicedev-fixed-main
worktreectl new --run <run-id> --repo /workspace/alicedev-fixed-main
```

`worktreectl` owns the deterministic branch/worktree mapping (it does not make
the fixed-main checkout a run workspace). The fixed-main checkout remains on
clean `main`; the returned worktree path is the only path handed to the
conductor and its sub-agents. Archive it with
`worktreectl archive --run <run-id>` after the run.

All verification for this bootstrap must use a throwaway clone/remote. Never
point it at the production paseo workspace (`/srv/alicedev`).
