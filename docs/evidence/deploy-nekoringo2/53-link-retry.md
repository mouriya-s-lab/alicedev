# 53 — `/链接` retry after runtime config repair (Telegram, @ririOuObot)

Follows `50-telegram-e2e.md`, whose only FAIL was `/链接`. Root cause: the running
AstrBot container had loaded a stale in-volume config
(`/AstrBot/data/config/alicedev_config.json` → `admin_users=['webchat:astrbot']`),
so `telegram:865341181` was not recognized as admin and the dispatcher silently
denied `/链接`. Host `deploy/.env` and the host rendered config already contained
`telegram:865341181`; only the volume copy was stale, because the previous change
was applied with `docker compose restart`, which does NOT re-run the `astrbot-init`
one-shot that seeds the volume config.

## Runtime config repair (no `compose down`)

```
cd /srv/alicedev
C="docker compose -f deploy/docker-compose.yml --env-file deploy/.env"
make render-config                       # regenerate rendered config from .env
grep -c telegram:865341181 deploy/astrbot/alicedev_config.rendered.json   # -> 1
$C up -d --force-recreate --no-deps astrbot-init   # re-run seed one-shot
$C restart astrbot                                  # reload volume config
```

Verification inside the container after restart:

```
docker exec alicedev-astrbot grep -o telegram:865341181 \
  /AstrBot/data/config/alicedev_config.json
# -> telegram:865341181     (in-volume config now includes the telegram admin)

docker logs alicedev-astrbot | tail
# [Core] [INFO] [alicedev.main:184]: alicedev initialized: 10 commands, 4 templates
# [Core] [INFO] [telegram.tg_adapter:255]: Telegram Platform Adapter is running.
```

## `/链接` retry — PASS

```
tg send ririOuObot '/链接 s_kukcnugeoq'      # -> outgoing Telegram msg 9588 @ 12:45:49Z
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 4 --yaml
```

Bot reply (msg 9589 @ 2026-09-19T12:45:52Z), one-time URL redacted:

```
@KunoriHaruka https://alicedev.237575.xyz/t/<redacted>
```

The one-time URL was NOT opened (single-use; opening would consume it).

### Server cross-check — `tokens_issued` audit row written

Queried a copy of the DuckDB (`alicedev.duckdb` + `.wal`; the live process holds a
write lock, and the just-written row lives in the WAL sidecar):

```
count = 7
token_id   = t_717ca23d9ec3447ab599f7fc4ced8023
session_ref= s_kukcnugeoq
user_key   = telegram:865341181        # admin recipient
issued_by  = telegram:865341181        # admin issuer
target     = /h/srv_rAoczNBNKn6i/workspace/wks_7e243c7a9c97a5b7
             ?open=agent%3Af85f2753-fbdd-4db0-8d9f-c2d873668887&embed=1
issued_at  = 2026-09-19 12:45:50
expires_at = 2026-09-19 18:45:50        # 6h TTL (_TOKEN_TTL_S = 21600)
```

`_handle_links` order is `gateway.issue_token` → `_record_token` → `_send_link`
(all sequential in one `try`), so the delivered URL implies the audit row was
written; the row above confirms it.

## Result

Telegram real-platform E2E is now **PASS on all paths**, including `/链接`. The
`50-telegram-e2e.md` FAIL was a runtime-config staleness (volume seed not re-run on
`restart`), not a code defect — no source or committed config changed. The
operational lesson (`restart` reuses the stale in-volume config; use
`up -d --force-recreate --no-deps astrbot-init` + `restart astrbot`, or
`up -d --force-recreate astrbot`) is already documented in the runbook.
