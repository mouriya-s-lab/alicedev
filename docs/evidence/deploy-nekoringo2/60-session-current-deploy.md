# 60 — Current session, recovered store, and deployment evidence

Observed production evidence for the current deployment. Values that could
identify a one-time link, credential, or private runtime object are omitted.
Telegram message IDs and published commit/hash evidence are retained where
needed to make the observations reproducible.

## Deployed revision

- Deployed source commit: **`9e032d2`** — `fix: checkpoint migrated DuckDB state safely`.
- The runtime observations below were taken after that deployment and after
  the DuckDB recovery/cutover.

## DuckDB recovery and reversible cutover

The original live database pair was preserved immutably before replacement:

- Location: `/root/alicedev-recovery/20260919T203318Z/live/`.
- Base database: 3,158,016 bytes, SHA-256
  `74484745c0a7cf99dcd6ce3a22745a7c9614c81d9ba5bb9647754080db2e7fcd`.
- WAL: 37,776 bytes, SHA-256
  `9be986b62226d3cf43009188f98621853f16c26bd92e78c22760df2b24eac9d5`.
- A base-only recovery was rejected: it exposed one session and zero
  token-audit rows, while the WAL contained unique user data.

Recovery used DuckDB **1.5.5** through an in-memory connection. The full
base-plus-WAL pair was attached and replayed successfully, then checkpointed.
The resulting candidate was:

- `/root/alicedev-recovery/20260919T203318Z/experiments/store-v2-latest/alicedev.duckdb`
- 8,663,040 bytes, SHA-256
  `b05808d6010c047fd930fdb85ca0004bfe13732ab3536a8a8c52eb28b6531f9a`.
- No WAL remained after `Store.open` plus `CHECKPOINT`.

The cutover remained reversible. The pre-replacement pair was quarantined at
`/root/alicedev-recovery/20260919T203318Z/cutover-original/20260919T211009Z-401300/`:

- Quarantined base SHA-256:
  `74484745c0a7cf99dcd6ce3a22745a7c9614c81d9ba5bb9647754080db2e7fcd`.
- Quarantined WAL SHA-256:
  `9be986b62226d3cf43009188f98621853f16c26bd92e78c22760df2b24eac9d5`.

A post-start plugin-data archive was also retained:

- `/root/alicedev-backups/alicedev-plugin-data-20260919T211148Z-402270.tgz`
- SHA-256 `5686c5e24430c5ad8be0d7f8aac5c9f16005cb4fd4d4372b824b1a9b0ea01827`.

### Inventory comparison — PASS

The candidate and post-start snapshot inventory matched exactly:

| Check | Observed result |
| --- | --- |
| Tables | 11 |
| Schema | version 2; migration markers include `v2_legacy_timestamps_utc`; values `[1, 2]` present |
| Sessions | 6 active; all 6 names nonblank |
| Current-session mappings | 4; zero orphan, mismatched, or non-selectable rows |
| Requirements | 4 |
| Messages | 8 |
| Replies / outbound | 9 / 9 |
| Reports | 1 |
| Token-audit rows | 8 |
| Referential checks | every missing/mismatch count zero |
| Candidate vs snapshot | primary-key hashes, timestamps, and token digests equal |

The live AstrBot logs included `alicedev initialized: 10 commands, 4
templates`. No plugin, WAL, or replay errors were observed.

## Service state — observed

At the final convergence check:

- `astrbot`, `caddy`, `gateway`, `paseo`, and `t2i`: **running/healthy**.
- `astrbot-init` and `reports-init`: **exited** as expected one-shot jobs.
- `napcat`: **running**; no healthcheck is configured for that container.
- Public gateway health endpoint: **HTTP/2 200** with the expected JSON health
  response.
- NapCat WebUI redirect/gate: `/webui/` ultimately **HTTP 200**.
- NapCat host exposure: loopback `6099` only; no host `6199` listener.

These are service and endpoint observations, not a claim that QQ is online.

## Telegram live session-name and routing sequence

The following sequence was read from the live Telegram DM and correlated with
runtime behavior. One-time links and full URLs are redacted.

1. **9592 → 9593, `/alicedev` help** — the command was sent as message 9592;
   the bot returned the rendered help card as photo message 9593. The visual
   artifact is [`60-help-card.jpg`](60-help-card.jpg), inspected at 800×1571.
   It shows the current session card, available commands, current-routing
   rules, the no-success-ack rule, and the no-current-session rule. The
   rendered current name was `历史GitHub Issue · 2026-09-19 18:56`.
2. **9594 → 9596, named current session** — `/需求 当前会话命名验收：支持中文主题切换`
   produced creation/current acknowledgement 9595, exactly:
   `当前会话：「需求 · 当前会话命名验收：支持中文主题切换」`.
   Substantive analysis arrived as 9596.
3. **9597 → 9598, ordinary supplement** — an ordinary supplement (9597)
   produced no bot acknowledgement after 4 seconds. The substantive reply
   9598 arrived after 15 seconds and addressed that supplement.
4. **9599 → 9600, Issue link plus prose** — an Issue #1536 URL accompanied by
   prose in message 9599 produced no creation acknowledgement. Substantive
   message 9600 stayed in the same current conversation and combined the
   supplement/current theme with Issue #1536 rather than opening a new
   URL-triggered session.
5. **9601 → 9602, exact URL-only Issue link** — the URL-only Issue #1536
   message 9601 produced creation/current acknowledgement 9602:
   `当前会话：「Issue #1536 · Native menu strings do not follow the UI langu」`.
   The displayed name is bounded to 60 code points.
6. **9603 → 9604, fresh link** — `/链接` was sent as 9603. Bot message 9604
   contained the current name and a fresh one-time URL; the URL is redacted.
   Its issuance and browser consumption are documented in
   [`53-link-retry.md`](53-link-retry.md), with screenshots
   [`61-link-browser-open.png`](61-link-browser-open.png) and
   [`62-link-session-open.png`](62-link-session-open.png).

The sequence demonstrates the intended distinction between ordinary prose
(which remains in the current named session), an exact URL-only trigger (which
creates the bounded Issue session), and a fresh administrator link. It does
not claim that every message received a separate success acknowledgement;
the missing acknowledgements above are part of the observed contract.

## Fresh-link browser evidence

The gateway log recorded issuance at **21:24:40**, a Telegram preview `GET`
at **21:24:42** returning **200** without consumption, and the real browser
sequence at **21:25:16**: `GET` **200**, consuming `POST` **303**, destination
`GET` **200**. The browser showed the running status page, then `进入会话`
opened the actual embedded Issue #1536 workspace. A separate replay was
rejected as already used/invalid. The token and all one-time URLs are omitted;
see `53-link-retry.md` for the redacted request-method proof.

## NapCat operator handoff

The NapCat container/protocol endpoint is deployed. WebUI authentication
succeeded without exposing its token. A headed, user-visible browser session
is currently at `/webui/qq_login` with `密码登录` selected; both the `QQ账号`
and `密码` fields are empty. No QQ login or QR action was submitted, and the
existing SSH tunnel remains running.

QQ is **intentionally not logged in**: the login gate remains pending the
operator's manual QQ-password and security-verification step. No QQ password
was entered, no security verification was completed, and no QQ online state is
claimed. The observed runtime had zero QQ-online markers and pending-login
markers present.

## Evidence files

- [`53-link-retry.md`](53-link-retry.md) — redacted root-cause and
  request-method proof.
- [`60-help-card.jpg`](60-help-card.jpg) — rendered Telegram help-card visual
  evidence.
- [`61-link-browser-open.png`](61-link-browser-open.png) — successful status
  landing page.
- [`62-link-session-open.png`](62-link-session-open.png) — embedded workspace.
- [`50-telegram-e2e.md`](50-telegram-e2e.md) and
  [`51-telegram-db.txt`](51-telegram-db.txt) — earlier Telegram and server
  correlation evidence.
