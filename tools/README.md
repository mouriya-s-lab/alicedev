# tools/

Shims and operator CLIs. Contracts are in `ARCHITECTURE.md` (§4 paseo CLI, §7 scheduler, §11 deploy, §13.2 upgrade-bot).

| Tool | Runs where | Purpose |
|---|---|---|
| `paseoctl` | host / anywhere with docker | `docker exec -i --user paseo alicedev-paseo paseo <args> --json`; the same invocation the bot's PaseoControl uses. `--local` runs `paseo` directly. |
| `mainsync` | paseo container (`--local` default) or host (`--container alicedev-paseo`) | Fast-forward-only alignment of the fixed-main checkout to `origin/main`; any other state prints `main_sync_failed` and exits 1. |
| `bootstrap-fixed-main.sh` | host | Clone `main` into `/workspace/alicedev` inside the paseo container (alignment-only; never an AI cwd), then `mainsync`. |
| `deployctl` | paseo container (deploys) / host (`bootstrap`) | Owns the host alicedev checkout that AstrBot mounts: `bootstrap`, `status`, `apply`, `rollback`, `run`. |
| `deployrun` | paseo container | `deployctl run`: apply → verify → roll back on failure; outcome `active` / `rolled_back` / `deploy_failed`. |
| `e2e_driver.py` | paseo container | Before/after on the resident e2e stack: same WebChat messages against the running SHA and a candidate SHA, screenshots + `{F, E, d}`. |
| `tgctl` | host / paseo container | `docker exec alicedev-tg-cli tg …`: `send`, `recent`, `photo`. |
| `tg-approve-login` | operator machine | Accept a Telegram QR login for the tg-cli container from an authorized local session. |

## Deploy (`deployctl`, `deployrun`)

The checkout lives at `/srv/alicedev/app` on the host and is mounted at `/deploy/app` in the paseo container; AstrBot mounts its `bot/` and `templates/`. It is owned by uid 1000 (`paseo` in the paseo container); `apply` / `run` / `rollback` refuse to run as any other uid, so a root run cannot leave git files the owner can no longer rewrite (the e2e checkout follows the same rule).

```sh
# inside alicedev-paseo (the upgrade-bot `deploying` state does this):
/deploy/app/tools/deployrun --app /deploy/app --commit <full main sha>
```

`apply` / `run`:
1. `git fetch <source> <ref>` (default source `origin`; GitHub auth via `GITHUB_TOKEN` through a temporary `GIT_ASKPASS`, never argv). The ref must be fetchable by name: pass the full 40-char SHA, since GitHub rejects abbreviated SHAs (`couldn't find remote ref`),
2. `git checkout --detach <sha>` and write the untracked `bot/REVISION`,
3. activate: `reload` = `POST $ASTRBOT_API_BASE/api/v1/plugins/alicedev/reload` with `X-API-Key: $ASTRBOT_API_KEY` (scope `plugin`); a plugin that failed to load is reloaded with `/api/v1/plugins/failed/alicedev/reload` instead (reloading an unregistered name would reload every plugin). `restart` = `docker restart <container>`. `auto` (default) restarts when `bot/requirements.txt` changed, otherwise reloads.
4. verify: poll the bot's `/v1/health` until `revision` equals the SHA and `generation` changed (reload) or the container restarted; if `ALICEDEV_INTERNAL_TOKEN` is set, `/v1/status` must report no `dsl_errors`.

`run` rolls back to the previous HEAD on any failure (rollback prefers the failed-plugin route). Exit 0 only for `active`.

Protocol/data cutovers are not ordinary bot releases: use the maintenance sequence in `.omp/rules/deploy.md` §4. `apply --activate restart` verifies without automatic rollback; do not use `run`/`deployrun` after schema v5 retirement or new-protocol writes. Prebuild the overlay from an independent target-SHA context without moving this live checkout, and keep gateway/AstrBot stopped across the harness/bot boundary.


Bootstrap (host, once; `make bootstrap-app COMMIT=<sha>` from a developer checkout does this over ssh with the host's `.env`):

```sh
set -a; . /srv/alicedev/deploy/.env; set +a
python3 tools/deployctl bootstrap --app /srv/alicedev/app --repo-url https://github.com/mouriya-s-lab/alicedev.git --commit <sha>
chown -R 1000:1000 /srv/alicedev/app
```

## Fixed-main (`bootstrap-fixed-main.sh`, `mainsync`)

`/workspace/alicedev` is the alignment-only checkout of `main` for the upgrade-bot scenario. Sessions work in worktrees derived from it (`paseo workspace create --isolation worktree --path /workspace/alicedev --mode branch-off --base main`); only `mainsync` mutates it. It fails closed on a dirty tree, detached/other branch, wrong upstream, merge/rebase in progress, or local commits ahead of / diverged from `origin/main`.

```sh
tools/bootstrap-fixed-main.sh --container alicedev-paseo   # host
tools/mainsync align --repo /workspace/alicedev --container alicedev-paseo
```

## e2e (`e2e_driver.py`)

The resident stack (`deploy/e2e`, containers `alicedev-e2e-astrbot` / `alicedev-e2e-t2i`) mounts `/srv/alicedev/e2e-src` (`/deploy/e2e-src` in paseo) and sits on the internal `alicedev-e2e` network that only paseo also joins.

```sh
# inside alicedev-paseo; candidate SHAs can be fetched from the session worktree:
python3 /deploy/app/tools/e2e_driver.py run \
  --baseline <running sha> --candidate <candidate sha> --source /workspace/<worktree> \
  --message '/alicedev' --output <reports_dir>/e2e
```

Per side: move the checkout (deployctl, `restart` activation — AstrBot and t2i are restarted, which also clears render caches), verify the revision, log into WebChat with agent-browser (`E2E_DASHBOARD_PASSWORD`), send the messages, wait for each reply, screenshot. Bot replies are proactive (outbox) messages that WebChat stores in the conversation history rather than the request stream, so the driver reloads the conversation until the reply (text or image) appears and holds across two reloads. Output: `request.json`, `{baseline,candidate}/{run.json,capture.png,browser.log}`, `metric.json` (`F`, `E`, `d`), `evidence.json`. The e2e stack has no paseo and no production credentials: AI commands are validated on production, the e2e stack covers program commands, rendering and DSL.

## Telegram (`tgctl`, `tg-approve-login`)

`alicedev-tg-cli` keeps its own Telegram authorization in the `alicedev_tg_cli_data` volume. To log it in without a phone:

```sh
# 1. on the host: start a QR login inside the container (prints "QR tg://login?token=...")
docker exec -i alicedev-tg-cli python /opt/alicedev/tg-qr-login.py --timeout 180
# 2. on the operator machine, with the authorized local kabi-tg-cli session:
~/.local/share/uv/tools/kabi-tg-cli/bin/python tools/tg-approve-login 'tg://login?token=...'
```

Exit 3 from step 1 means the account has two-step verification; then copy an authorized `tg_cli.session` into the volume instead and stop using that session elsewhere (one auth key used from two places gets revoked by Telegram).
