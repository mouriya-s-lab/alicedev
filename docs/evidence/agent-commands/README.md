# agent 调用指令与管家：生产验收（2026-09-26）

对应 issue https://github.com/mouriya-s-lab/alicedev/issues/12 。环境：nekoringo2 生产，私聊 `RIRI OuO` ↔ `@ririOuObot`，tg-cli 真投递，模型 `muse-spark-1.3-contributor`。

## 上线

- 部署前备份：`/srv/alicedev/backups/pre-steward-20260925T193925Z/`（DuckDB `87b92db3…`、WAL `de41aded…`）；镜像标签 `alicedev/{paseo,astrbot}:rollback-pre-steward`。
- `deployrun` → `cc1ae71`：`outcome: active`，`dsl_errors: []`；astrbot 日志 `alicedev initialized: 18 commands, 6 scenarios, 0 DSL errors`。DuckDB 在重载时就地升级到 v4。
- paseo：宿主上 `alicedev/paseo-base:local` 已不存在，按部署规则加临时 swapfile（`/swapfile-alicedev`，用后删除）重建 base，再构建并 `up -d --no-deps paseo`；容器 healthy，`/usr/local/bin/alicedev` 存在、`alicedev-reply` 已移除，`/opt/alicedev/omp-extension/skills/alicedev/SKILL.md` 存在。
- 验收中发现单对话场景拿不到收藏要的消息 id（见下），修正后 `deployrun` → `92a16c7`，`outcome: active`。

## TG 序列

| 消息 | 路径 | 观察 |
|---|---|---|
| 9742 → 9743, 9744, 9745 | `/需求 …` 并要求 AI 先查重、再把子问题单独开需求 | 9743「已开始 %15」；9744「已开始 %16 需求 · 告警渠道支持 Telegram…」由 bot 留痕；9745 AI 回复写明 `/需求列表 → ok`、`/需求 → created %16` |
| 9747 → 9748 | `/会话 %16` | 会话卡：创建人 `telegram:865341181`（%15 的发起人），无「当前会话」标记——agent 开的会话没有抢当前会话（[`01-session-16-card.jpg`](01-session-16-card.jpg)） |
| 9749 → 9750 | 要求 %15 收藏上一条消息（修正前） | AI 如实报告 `/会话 %15 → command_not_allowed`、不带 id 的 `/收藏 → usage_error`，未收藏。据此把 `会话` 加进四个单对话场景的 `agent_commands`（ARCHITECTURE §9、§13.1） |
| 9751 → 9752, 9753 | 修正后再次要求收藏 | 9752「已收藏 #3」；9753 AI 写明 `/会话 %15 → ok 拿到 m_4bemqvvtcj`、`/收藏 --quote m_4bemqvvtcj → #3` |
| 9754 → 9755 | `/收藏夹` | #3 原文为该条群友消息的平台原文，原作者与收藏人均为发起人（[`02-favorites.jpg`](02-favorites.jpg)） |
| 9756 → 9757, 9758, 9759 | `/管家 去研究一下：OpenAlice 仓库里行情数据是从哪些数据源接入的…` | 9757「已开始 %17 管家」；9758「已开始 %18 调查 · …」由管家经 `/帮我调查` 派出；9759 管家只回「已派给 %18」 |
| 9760 | %18 自己的回复 | 「%18 行情数据源调查完成：16 个 vendor + UTA 券商实时层」+ 报告链接；管家没有转述 |
| 9761 → 9762 | `/管家 %18 研究得怎么样了？` | 没有新开管家会话；%17 回复基于 %18 的状态与最近往来给出三层数据源的概括并指回 %18 |
| 9763 → 9764、9765 → 9766 | `/群列表`、`/状态` | 两张新卡片（[`03-chats-card.jpg`](03-chats-card.jpg)、[`04-status-card.jpg`](04-status-card.jpg)）：状态卡 revision `92a16c7`、18 指令、6 场景、0 DSL 错误 |

## CLI（生产 paseo 容器内，`docker exec -u paseo alicedev-paseo …`）

| 调用 | 结果 |
|---|---|
| `alicedev commands --agent a_5e5bgfkxqa`（%15） | exit 0；`audience: all`，列出 需求 / 需求列表 / 收藏 / 收藏夹（部署修正后另有 会话） |
| `alicedev run --agent a_5e5bgfkxqa '/归档 %16'` | exit 1，`{"error":"command_not_allowed","message":"本状态不能调用 归档"}` |
| `alicedev run --agent a_5e5bgfkxqa --chat telegram:FriendMessage:1 '/需求列表'` | exit 1，`{"error":"chat_not_allowed"}` |
| `alicedev run --agent a_vyxpxw3i4x '/归档 %17'`（管家归档自己） | exit 1，`{"error":"self_archive"}` |
| `alicedev run --agent a_vyxpxw3i4x '/群列表'` | exit 0，`data.rows` 为各群 chat_key、未结束会话数、最近活动 |

## 未在真实平台上覆盖

- **非管理员被拒**（`/管家`、向管家会话发话）：TG 验收账号、生产与 e2e 的 WebChat 用户都是管理员，QQ 未登录，没有非管理员身份可用。由 `bot/tests/test_steward_flow.py` 覆盖；有非管理员账号后在群里补一次。
