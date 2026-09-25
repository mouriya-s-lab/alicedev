# 70 — `/tmp` 故障恢复后的 Telegram 验收（2026-09-26）

线上版本 `e286dad`（`deployctl status` 的 head、revision_file、health.revision 一致）。一次性链接已脱敏。

## 故障窗口

- 开始：2026-09-25 01:07 HKT，dockerd 首次记录 `OCI runtime exec failed: write /tmp/runc-process…: no space left on device`（健康检查）。
- 原因：孤儿 `tcpdump -w /tmp/nbx-final-direct-cycle1.pcap`（9-20 起，文件已删、句柄未关，4.0G）写满 3.9G 的 `/tmp` tmpfs。
- 结束：约 22:52 HKT，停掉该进程后 `/tmp` 占用 4%，全部容器 healthy。
- 故障期间 astrbot 日志里没有 `paseoctl` 或插件错误，说明窗口内没有 AI 指令打到坏掉的 shim。

## 验收序列（私聊 `RIRI OuO` ↔ `@ririOuObot`，tg-cli 真投递）

| 消息 | 路径 | 观察 |
|---|---|---|
| 9729 → 9730 | 程序指令 `/alicedev` | 7 s 内回帮助卡图片：5 条 AI 指令、13 行程序指令、「还没有当前会话」，无渲染错误（[`70-help-card.jpg`](70-help-card.jpg)） |
| 9731 → 9732 | 程序指令 `/会话列表` | 5 s 内回列表卡：%7 %6 %4 %3 %2 %1，均「对话中」（[`71-session-list.jpg`](71-session-list.jpg)） |
| 9733 → 9734, 9735 | AI 指令 `/帮我调查 …`：bot → paseoctl → paseo → agent → `/v1/reply` → 出站 | 9734「已开始 %14 调查 · …」；9735 AI 回复「OpenAlice 仓库根目录 README 标题是 OpenAlice。」，发出后 25 s 到达 |
| 9736 → 9737 | 普通消息补充到当前会话（`addressed` 路由） | 19 s 后 9737 仍带 `%14` 标记，回答「主要编程语言是 TypeScript」 |
| 9738 → 9739 | 程序指令 `/链接` | 2 s 内回一次性链接；预览 GET 200 不消费；agent-browser 新浏览器打开 → 会话页（[`72-link-session-page.png`](72-link-session-page.png)）→「进入会话」→ paseo 工作区，时间线含 agent 的工具调用与 `chat_reply`（[`73-link-workspace.png`](73-link-workspace.png)）；同一链接重放 POST 403 |
| 9740 → 9741 | 程序指令 `/归档 %14` | 6 s 内「已归档 %14 …」；`paseo ls --json` 不再列出该 agent |

## 未覆盖

- `/升级bot` 多状态链路：本次没改代码，故障不涉及它的专属路径（`mainsync`、`deployrun` 与上面的路径共用同一个 `docker exec`），没有重跑。
- QQ：账号仍未登录（`docs/runbook.md` §3）。
