# 垂直切片 spike 结果（ARCHITECTURE §14 step 1）

日期：2026-09-18。执行者：BridgeCore。所有结论均有运行时证据（下附命令/输出/文件引用）。截图见 `docs/evidence/spike/`。

本 spike 端到端打通 **WebChat `/需求` → paseo omp agent（不泄露平台 msg id）→ `chat_reply` → reply-cli → bot `/v1/reply` → 平台投递**，并验证 `session_stop` 提醒、`/继续` 注入、sweeper close + 自动恢复、以及回复的 at-most-once/重启幂等。

## 1. 运行拓扑（本地 dev）

| 组件 | 位置 | 端口 |
|---|---|---|
| paseo daemon（原生 bun，spike 用）| hub 进程 `paseo-spike`，`PASEO_HOME=~/.paseo-alicedev-spike` | `0.0.0.0:6777` |
| AstrBot + alicedev 插件 | OrbStack 容器 `alicedev-astrbot`（`soulter/astrbot:v4.28.1`）| dashboard `6185`，内部 API `6200` |
| t2i（卡片渲染依赖）| 容器 `alicedev-t2i` | `8999` |
| omp agent（每会话）| paseo 拉起的 bun 子进程，`cwd=/tmp/alicedev-ws` | — |

- 插件目录 `bind-mount ./bot`；运行时数据在 `data/plugin_data/alicedev/`（duckdb + images）。
- 容器→daemon 走 `host.docker.internal:6777`；omp/reply-cli 在宿主原生运行，经 `ALICEDEV_INTERNAL_API` 回 bot。
- daemon 配置启用 `omp` 与 custom provider `omp-alicedev = {extends: omp, command: [omp, -e, <harness>/omp-extension]}`。

## 2. §4 传输判定（MCP）——回写 ARCHITECTURE §4 表

- `POST /mcp/agents` 裸 `tools/call` 可用，**无需 `initialize` 握手，无需 daemon 自身 caller 上下文**。鉴权 `Authorization: Bearer $PASEO_PASSWORD`；响应为 SSE（`data:` 行含 `.result.structuredContent`）。
- `create_agent` 的 `provider` 与 `initialPrompt` **均为必填**——无法创建“空闲待发”的 agent。因此 ingress 命令改由 `initialPrompt` 承载（不再单独 `send` 首轮）。`provider` 字段传 `omp-alicedev/<provider>/<model>`（实测 `omp-alicedev/anthropic/claude-fable-5-1`），`settings.thinkingOptionId=high`，`labels.alicedev=<session_ref>`，`workspace.kind=create`，`relationship=detached`，`background=true`。
- `list_agents` 按 `labels.alicedev` 找回（`find_by_label`）实测可用。
- `kill_agent` = **可恢复 close**：调用后 `get_agent_status` 返回 `closed`，agent 记录保留。
- **closed→active 自动恢复**：对 closed agent `send_agent_prompt` 触发 paseo 自动 `ensureAgentLoaded`，status `closed→running`，模型继续处理并回复。实测（§6）。
- 结论：**WS 备选与 fork `close_agent_request`（§9 第 3 项）无需实现**。

`PASEO_HOSTNAMES` 必填：daemon 对未知 `Host` 返回 `403 {"error":"Invalid Host header"}`（比较时剥离端口，`host.docker.internal:6777` 匹配 `host.docker.internal`）。spike 设 `host.docker.internal,localhost,127.0.0.1`；**生产网关须设 `PASEO_HOSTNAMES=alicedev.237575.xyz`（已告知 DeployRunbook）**。

## 3. 首轮：`/需求` → 回复（不泄露 msg id）

WebChat 发送 `/需求 请为登录页添加"记住我"复选框` → bot 回 `已创建 s_vefg56v7ne`（截图 `01-*.png`）。

omp 会话文件 `~/.omp/agent/sessions/--private-tmp-alicedev-ws--/2026-09-18T20-21-26-*.jsonl` 中，渲染后的 **initialPrompt（模型看到的首条 user 消息）** 为：

```
你是 alicedev 社区（维护 OpenAlice 项目）的开发助理。……
会话标识：s_vefg56v7ne
来自群聊：webchat:FriendMessage:webchat!astrbot!40fe1321-83d2-4743-8724-d273eae45c95
提出人：astrbot
需求原文：请为登录页添加"记住我"复选框
请完成：1. ……复述理解……  2. ……可行性/范围……  3. ……待补充问题……
---
## 回复方式（alicedev）
你必须通过 `chat_reply` 工具把答复发回群聊；在此之前不要结束本轮。
- 可用回复类型 kind：text, image_template
- 图片模板 image_template：requirement_summary, generic_card
- 纯文本超过 600 字会自动转为图片卡片；长内容请直接用 image_template。
```

**关键：平台 msg id（`m_ubijgngrvc`）不在 prompt 中**——它只存在于 omp 扩展状态（`alicedev.pending {session, msg}` custom event），模型不可见。满足“不泄露 msg id”硬性要求。

时间线（同一 jsonl 的 custom events）：
```
alicedev.pending {session: s_vefg56v7ne, msg: m_ubijgngrvc}   # ingress 命令被扩展拦截
write xd://chat_reply                                          # 模型调用 chat_reply 工具
alicedev.consumed {msgs: [m_ubijgngrvc], reply_id: 79e98e11-…}# 扩展消费 pending，reply-cli 投递成功
```

reply-cli → bot `/v1/reply` → `context.send_message(umo, chain)` → 落库到对应会话（AstrBot `data_v4.db` 的 `platform_message_history`，`user_id=40fe1321…`、`sender_id=bot`、正文为模型的中文答复）。手动等价投递返回 `{"status":"sent","platform_message_ids":["alicedev-…"]}`。

## 4. `session_stop` 提醒恰好一次

在模型未主动回复的会话里，omp 扩展在 `session_stop` 注入一次续跑提醒：jsonl 中出现且仅出现一次
```
alicedev.reminded {msgs: [m_…]}
(custom_message) session-stop-continuation
```
再次 stop 不重复提醒（扩展状态标记已提醒）。

## 5. `/继续` 注入既有会话

实现于 `bot/alicedev/commands/continuation.py`（`/继续 <s_ref> <text>`，或引用 bot 消息 + 文本经 `outbound` 解析，校验同群）。

WebChat `/继续 s_vefg56v7ne 补充：也要支持第三方登录…` → bot 回 `已转达至 s_vefg56v7ne`。同一 omp 会话文件出现**第二个** `alicedev.pending {msg: m_z27x3cshf5}` → `write xd://chat_reply` → `alicedev.consumed {reply_id: 55eaff6c-…}`。注入走 `SessionActor.inject`→`_inject_locked`（持会话锁、`_wait_until_idle`、`send_agent_prompt`），严格串行。

## 6. sweeper close + `/继续` 自动恢复

- 经 MCP `kill_agent(<agent>)`（sweeper 的 `close` 路径）→ `get_agent_status` = `closed`。
- 随后 WebChat `/继续 s_vefg56v7ne …` → 立即观测 `get_agent_status` = `running`（paseo 自动 `ensureAgentLoaded`），产生**第三个** `alicedev.pending {msg: m_os4q6lp4aa}` → `alicedev.consumed {reply_id: 1cdd8c1e-…}`。

即 `closed --(inject)--> running --> idle`，无需显式 resume 工具。ARCHITECTURE §4 状态机 `closed→active` 得证。

## 7. 回复 at-most-once + 重启幂等

直接对 `/v1/reply` 打点（`X-Alicedev-Token`）：

| 步骤 | 请求 | 结果 |
|---|---|---|
| 首次 | `reply_id=replaytest-1`, payload P | `200 {"status":"sent","platform_message_ids":["alicedev-f3fcc…"]}` |
| 同 id 同 payload | `reply_id=replaytest-1`, payload P | `200 {"status":"replayed", 同一 id}` |
| 同 id 异 payload | `reply_id=replaytest-1`, payload P' | `409 {"error":"reply_id_conflict"}` |
| **`docker restart alicedev-astrbot` 后** 同 id 同 payload | | `200 {"status":"replayed", 同一 id}` |
| 重启后 同 id 异 payload | | `409 {"error":"reply_id_conflict"}` |

`reply_deliveries`（duckdb）持久化，重启不丢：reply-cli 在 bot 重启（mid-reply 崩溃）后重试得 `replayed`，不会重复投递。claim 在 `store.lock` 下 check-then-write，保证并发原子。

## 8. 发现与遗留（供后续 slice）

1. **`localhost` vs `127.0.0.1`（dev 环境）**：本机 `localhost` 解析到无法命中已发布端口的路径（IPv6/OrbStack），`127.0.0.1:6200` 正常。spike 的 `ALICEDEV_INTERNAL_API` 由 `localhost` 改为 `127.0.0.1`。**生产无此问题**：paseo 容器经 compose 网络服务名 `http://astrbot:6200` 直连（DeployRunbook 负责）。
2. **AstrBot WebChat ChatUI 渲染**：out-of-band 主动消息（`context.send_message`）会落库到 `platform_message_history`（`llm_checkpoint_id=NULL`），但 ChatUI 从 checkpoint-thread 重建会话，对 `ckpt=NULL` 行渲染不稳定（部分会话可见——截图 `02-*.png`，部分需活跃订阅才即时刷新）。**非 alicedev 缺陷**：真实目标平台（QQ 群）主动发送原生可达；投递本身已由 DB 行 + reply-cli `sent` 证实。
3. **`context.send_message` 返回 `bool` 而非平台 msg id**：`_ContextSender` 因此合成 `alicedev-<uuid>` 作为 `outbound` 的句柄。这意味着 QQ 上“引用 bot 消息触发 `/继续`”无法用平台真实 id 命中 `outbound`（合成 id ≠ 平台 id）。**显式 `/继续 <s_ref>` 不受影响**。生产回复路径若要支持引用解析，需从平台适配器取真实已发 msg id（供 LinksGithub/回复路径 owner 评估）。

## 9. 复现要点

- MCP 探针：`POST http://127.0.0.1:6777/mcp/agents`，头 `Content-Type: application/json`、`Accept: application/json, text/event-stream`、`Authorization: Bearer $PASEO_PASSWORD`；体 `{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":<tool>,"arguments":{…}}}`。
- reply-cli：`ALICEDEV_INTERNAL_API=http://127.0.0.1:6200 ALICEDEV_INTERNAL_TOKEN=<tok> node <harness>/reply-cli/alicedev-reply --session <s> --reply-id <id> --msgs <m> --json '{"kind":"text","text":"…"}'`。
- 秘钥在 gitignored `deploy/dev/.env.local`（PASEO_PASSWORD、ALICEDEV_INTERNAL_TOKEN、dashboard 密码）。
