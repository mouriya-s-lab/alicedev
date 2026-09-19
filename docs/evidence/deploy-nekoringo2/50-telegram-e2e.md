# Telegram DM E2E — `@ririOuObot`

Date: 2026-09-19 UTC. Production bot: `@ririOuObot` (Telegram ID
`8794402927`). Test account: `865341181` (`KunoriHaruka` / `はるか`). Chat
resolved by `tg` as `RIRI OuO` (DM, chat ID `8794402927`).

This run used the locally authenticated `tg` CLI (kabi-tg-cli). Every `tg
sync` was followed by `tg recent --chat 'RIRI OuO' --limit N --yaml` so that
bot messages were verified from the Telegram DM, not only from AstrBot logs or
DuckDB. The raw export is `50-telegram-export.yaml`; the requirement-list
Telegram photo downloaded from message `9581` is
`52-requirement-list-card.jpg`; server-side rows and OMP correlation are in
`51-telegram-db.txt`.

## Verdict

**PASS overall** (see below). At the time of this run `/链接` FAILED because
the running AstrBot container had a stale `admin_users` list containing only
`webchat:astrbot`; the host `.env` and rendered config already contained
`telegram:865341181`, but the AstrBot init-volume copy had not been refreshed
(`docker compose restart` does not re-run the `astrbot-init` seed one-shot). No
code, source, or deployment file was changed during this run. The volume config
was subsequently refreshed and `/链接` re-run to PASS — see `53-link-retry.md`
(one-time URL returned as Telegram msg `9589`, `tokens_issued` audit row written
for `s_kukcnugeoq` / `telegram:865341181`).

| Step | Result | Telegram evidence |
| --- | --- | --- |
| `tg whoami` + `/alicedev` | PASS | account `865341181`; fresh help reply `9573` |
| `/需求` + agent reply | PASS | ack `9575`, substantive out-of-band reply `9576` |
| `/继续` | PASS | ack `9578`, substantive reply `9579` |
| `/需求列表` card | PASS | photo `9581`, downloaded and visually verified |
| `/链接` | **PASS** (retry) | initially no reply (stale admin config); after volume refresh, one-time URL `9589` — see `53-link-retry.md` |
| bare GitHub Issue URL | PASS | session ack `9584`, interpretation card `9585` |
| `/收藏` without quote | PASS | exact usage reply `9587` |

## 0. Account and chat readiness — PASS

Command and observation:

```text
tg whoami
```

Observed at 2026-09-19T10:46:37Z:

```yaml
ok: true
user:
  id: 865341181
  name: はるか
  username: KunoriHaruka
```

The bot DM was unambiguous: `tg info 'RIRI OuO'` reported title `RIRI OuO`,
ID `8794402927`, type `User`, username `@ririOuObot`.

## 1. Help command — PASS

Command sequence:

```text
tg send ririOuObot '/alicedev'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 8 --yaml
```

Sent at 2026-09-19T10:46:37Z, Telegram message ID `9572`. After a fresh sync,
bot message ID `9573` arrived at 2026-09-19T10:46:40Z. The reply contained the
command help, including these original lines:

```text
alicedev 可用指令：

/帮我调查 — 调查一个问题并直接回复，或生成 Markdown 调查报告
/需求 — 记录群友需求并交给 AI 分析（别名：/req）
/继续 — 向已有会话补充内容并唤醒 AI 继续分析（别名：/continue）
/收藏 — 收藏一条引用消息（可包含图片）
/收藏夹 — 查看当前聊天的收藏夹
/需求列表 — 查看当前聊天的需求列表
/链接 [管理员] — 为会话生成一次性工作区分享链接（别名：/link）
/解读 — 预取并解读 GitHub Issue 或 Pull Request（别名：/interpret）
/归档 [管理员] — 归档一个 paseo 会话（别名：/archive）
/alicedev — 显示 alicedev 指令和 Prompt 模板

Prompt 模板：
⦁ github-issue · 链接触发：github_issue：读取 GitHub Issue，提炼背景、现状、风险与可执行建议
⦁ github-pr · 链接触发：github_pr：读取 GitHub Pull Request，评估改动、风险与验证缺口
⦁ investigate · /帮我调查：调查一个问题并直接回复，或生成 Markdown 调查报告
⦁ requirement · /需求（别名：/req）：记录群友需求并交给 AI 分析
```

## 2. `/需求` and real out-of-band agent delivery — PASS (critical path)

Command sequence:

```text
tg send ririOuObot '/需求 希望 OpenAlice 支持中文界面，并能切换语言'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 8 --yaml
```

The command was sent at 2026-09-19T10:46:58Z as Telegram message `9574`. The
immediate Telegram acknowledgement was message `9575` at 10:47:09Z:

```text
已创建 s_kukcnugeoq
```

After the acknowledgement, a **distinct later Telegram DM** arrived as
message `9576` at 2026-09-19T10:47:20Z. This is the substantive agent response
that was visible in `tg recent`, proving the out-of-band `context.send_message`
path reached the real Telegram DM:

```text
理解：给 OpenAlice 加中文界面，并支持语言切换。

模糊点：简体还是繁体也要？范围是全 UI 还是部分页面？切换是手动按钮还是跟随系统？

可行性：可行，属常规 i18n 改造。若现有文案硬编码，需先抽 key + 接 i18n 框架 + 中文语言包 + 切换持久化，工作量取决于硬编码规模；替代方案可先只汉化核心页面、边角先机翻占位。

需补充：1）简体/繁体？2）优先汉化哪些页面？3）默认语言和切换入口放哪里？4）是否接受社区协作翻译？
```

Server correlation is in `51-telegram-db.txt`:

- `sessions.session_ref=s_kukcnugeoq`, chat key
  `telegram:FriendMessage:865341181`, status `active`.
- OMP emitted `alicedev.pending` for `m_wjmo4kvvg4` at
  `2026-09-19T10:47:07.337Z`, then `alicedev.consumed` with reply ID
  `ba6549aa-e952-4b87-9738-2ea636ddddb4` at `10:47:20.568Z`.
- `reply_deliveries` for that reply is `state=sent`, platform message ID
  `alicedev-23ca4ad7aef24153ad8affeae6737ad2`.
- The persisted `requirements` row contains the exact Chinese requirement.

This combination—new Telegram message ID/timestamp plus sent DB row and
pending-to-consumed OMP event—is the critical real-platform proof.

## 3. `/继续` — PASS

Command sequence:

```text
tg send ririOuObot '/继续 s_kukcnugeoq 补充：优先支持简体中文'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 6 --yaml
```

Sent at 2026-09-19T10:51:12Z as Telegram message `9577`. The immediate bot
acknowledgement was message `9578` at 10:51:16Z:

```text
已转达至 s_kukcnugeoq
```

The subsequent substantive Telegram reply was message `9579` at 10:51:21Z:

```text
收到补充，更新下理解：OpenAlice 优先支持简体中文界面，并可切换语言，简体明确了，歧义收窄了。

可行性：可行，仍是常规 i18n 改造：抽文案 key + 接 i18n 框架 + 简中语言包 + 切换入口与持久化（localStorage/用户配置）。范围取决于硬编码规模，建议先汉化核心页面。

还需确认 3 点：1）优先汉化哪些页面？2）默认语言跟随系统还是默认简中，切换入口放哪里？3）是否接受社区协作翻译/校对？
```

The OMP correlation is a second pending/consumed pair for the same session:
`m_5rz5jk7h5d` pending at `10:51:15.826Z`, consumed by reply
`74b9e3c7-a972-456c-aad3-aa1c2ca3c0fd` at `10:51:21.663Z`; its delivery row is
also `state=sent`.

## 4. `/需求列表` image card — PASS

Command sequence:

```text
tg send ririOuObot '/需求列表'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 6 --yaml
```

Sent at 2026-09-19T10:51:36Z as message `9580`. Telegram message `9581`
arrived at 10:51:43Z from `8794402927`. `tg recent`/`tg export` represent the
photo as `content: ''` and `raw_json: null`, so the media-bearing message was
also inspected with the authenticated Telegram client. The media object was
`MessageMediaPhoto`, and it was downloaded to:

```text
docs/evidence/deploy-nekoringo2/52-requirement-list-card.jpg
```

The file is 23,630 bytes and was visually verified. It shows the alicedev
`需求列表` card with `共 1 条需求`, requirement `#4`, the exact Chinese
requirement text, author `KunoriHaruka`, `open` status, and footer `第 1/1 页 /
需求列表 1`.

## 5. `/链接` admin path — FAIL

Command sequence:

```text
tg send ririOuObot '/链接 s_kukcnugeoq'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 5 --yaml
```

Sent at 2026-09-19T10:53:23Z as message `9582` (DM timestamp
10:53:24Z). No bot reply appeared in Telegram during the remaining run; the
recent-message output continued to end at the outgoing `9582`.

The server cross-check found the precise cause:

- The running container's `/AstrBot/data/config/alicedev_config.json` had
  `admin_users=['webchat:astrbot']`, so `telegram:865341181` was not an admin
  in the loaded process configuration. The dispatcher therefore denied the
  command without a user-facing reply.
- The host `/srv/alicedev/deploy/.env` and
  `/srv/alicedev/deploy/astrbot/alicedev_config.rendered.json` both already
  contained the intended `telegram:865341181` entry; the AstrBot init-volume
  copy was stale.
- A read-only `tokens_issued` query for `s_kukcnugeoq` returned zero rows.

No one-time URL was returned, and no URL was opened. The requested non-admin
comparison was skipped because this run had only the one authenticated account;
there was no second user account to test.

## 6. Bare GitHub Issue link / `/解读` routing — PASS

The issue lookup found an existing issue:

```text
https://github.com/TraderAlice/OpenAlice/issues/1536
```

Command sequence:

```text
tg send ririOuObot 'https://github.com/TraderAlice/OpenAlice/issues/1536'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 5 --yaml
```

The bare link was sent at 2026-09-19T10:56:45Z as message `9583`. The bot
created interpretation session `s_jcq3eoeqeu` and replied with message `9584`
at 10:57:03Z:

```text
已创建 s_jcq3eoeqeu
```

A later Telegram photo message `9585` arrived at 10:57:34Z. Its text field is
empty because the interpretation was rendered as an image card. The visible
original card begins:

```text
Issue #1536 解读：原生菜单不跟随 UI 语言

【问题概括】作者称桌面端 renderer 有完整 i18n（en/zh/zh-Hant/ja），但主进程无 i18n，托盘、宠物右键菜单、应用菜单等原生字符串硬编码，用户切中文后仍中英混杂。

【工作区已核实】ui/src/i18n 确有四语言与持久化 key；apps/desktop/src/main.ts 当前使用 Menu.setApplicationMenu（OS roles），未找到 Tray/Show OpenAlice/Pet/伴侣菜单等字符串。

【关键风险】local 在 localStorage，主进程读不到，需新 IPC plumbing；desktop 包不依赖 ui 包，直接复用 locales 有依赖成本。
```

The card continued with `【需澄清】` and `【建议下一步】` sections. This is
an interpretation response, not a bare-link echo. OMP emitted pending
`m_3cis44kzen` at `10:57:02.413Z` and consumed it with reply
`c5787fbe-c25a-4dc9-ab7e-dd6666534834` at `10:57:34.926Z`; the DB delivery row
is `state=sent` with platform message ID
`alicedev-431ca48fcd2b433795b9a41e41f2177c`.

## 7. `/收藏` without a quoted message — PASS

Command sequence:

```text
tg send ririOuObot '/收藏'
tg sync ririOuObot
tg recent --chat 'RIRI OuO' --limit 5 --yaml
```

Sent at 2026-09-19T10:58:05Z as message `9586`. Bot message `9587` arrived at
10:58:07Z with the exact usage response:

```text
用法：/收藏（请引用一条消息后再发送）
```

## Evidence and safety notes

- `50-telegram-export.yaml` was produced with `tg export 'RIRI OuO'
  --format yaml --output docs/evidence/deploy-nekoringo2/50-telegram-export.yaml`
and contains 18 DM messages, including message IDs, sender IDs, timestamps,
text, and the two media messages as empty text records.
- `51-telegram-db.txt` contains the server-side sessions, reply deliveries,
requirements row, OMP pending/consumed lines, redacted adapter logs, and the
link-command configuration diagnosis.
- `52-requirement-list-card.jpg` is the downloaded Telegram card. No Telegram
URL was opened, no messages were deleted or purged, no group was created, and
no secrets were written to evidence.
- No source code, deployment config, or tests were changed for this run.
