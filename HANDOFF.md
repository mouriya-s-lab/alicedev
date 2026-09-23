# alicedev — 交接文档

> 新 session 顺序：先读 `AGENTS.md`，再读 `ARCHITECTURE.md`（契约），再读本文件（线上状态、待决事项、已知缺口）。运维步骤在 `docs/runbook.md`，验收证据在 `docs/evidence/`。

## 1. 工作方式约束（用户明确要求）

- 用户说完需求后自主完成，不为琐事反复确认；**动生产前必须过闸门**（说清做什么、影响谁、如何回滚）。
- **没有真实 e2e = 没做**。stub / fake runner / 单测不算验收。
- 架构必须可扩展，禁面条代码：指令与场景只写 DSL，传输 / 调度 / 可见性分开。
- GitHub owner `mouriya-s-lab`；日常 git/gh 用 RiriAgent。
- 凭据从 IaC/SOPS 取，不在对话里复述。面板密码类泄漏在非公网服务上不重要，不做说教、不强制轮换。
- 测试模型只用 `muse-spark-1.3-contributor`，未经允许不跑其他付费模型。

## 2. 定位

开发者社区（维护 OpenAlice: https://github.com/TraderAlice/OpenAlice）的 QQ/Telegram 机器人，AstrBot 插件形态，本质是「群聊 ↔ paseo agent 开发环境」的桥接层。契约见 `ARCHITECTURE.md` §0 五条不变量。

## 3. 线上现状（nekoringo2，2026-09-23）

- **版本**：`feat/upgrade-bot-loop` 已快进合入 `main`；宿主检出 `/srv/alicedev/app`、bot `/v1/health` 的 `revision`、nekoringo-iac `apps/alicedev` 的 `alicedev_revision` 都是 main 的最新提交（核对：`deployctl status --app /deploy/app`）。
- **容器**：`deploy` 项目 `caddy` `gateway` `astrbot` `snowluma` `paseo` `t2i` 常驻；`alicedev-e2e`（`init` + `astrbot` + `t2i`）与 `alicedev-tg-cli` 两个独立项目常驻。镜像：`alicedev/paseo:local`（上游 `mouriya-s-lab/paseo@7ab7c444d` 原样构建的基础镜像 + 运行层）、`alicedev/astrbot:local`（预装插件依赖）、`alicedev/gateway:local`。
- **数据**：DuckDB schema v3 迁移完成（旧会话、当前指针、需求列表都迁入，%1–%4 为旧会话）。
- **fixed-main** `/workspace/alicedev`：干净，停在 `80161d5`；下一个 `/升级bot` 派发时由 `mainsync` 快进。
- **Telegram**：`alicedev-tg-cli` 用自己的授权（本机会话批准的 QR 登录，账号 865341181）；验收私聊 `RIRI OuO` ↔ `@ririOuObot`。
- **QQ**：snowluma 在跑，**QQ 账号尚未扫码登录**（人工步骤，见 runbook §7）。
- **AstrBot dashboard**：记录在案的密码都返回 401，当前密码未知。`deployctl` 热重载用的是插件权限 API key（`ASTRBOT_API_KEY`，已进 SOPS，宿主副本 `/root/alicedev-astrbot-api-key`），不依赖 dashboard 密码。
- **回滚物**：`/srv/alicedev/backups/pre-v3-<ts>/`（v3 前的 DuckDB、`paseo_home`、旧 `bot/` `deploy/` `templates/`、镜像 ID）；镜像标签 `alicedev/{paseo,gateway}:rollback-pre-v3`、`alicedev/{paseo,astrbot,gateway}:rollback-pre-a49ad63`、`alicedev/paseo:rollback-pre-f7ef76f`、`alicedev/gateway:rollback-pre-boot`、`alicedev/gateway:rollback-pre-alice`（Alice 主题之前的网关）。
- **分享链接与网关页面**：已上线 Alice 主题（ARCHITECTURE §8）。兑换链接后落到本会话的会话页，从那里进入 paseo 工作区；bot 为此提供 `GET /v1/sessions/{id}`。网关镜像只由网关自己的 `docker compose … build gateway` 与 `up -d --no-deps gateway` 更新；重启网关会使尚未兑换的一次性链接失效。

验收覆盖面与上线中修复的问题见 `docs/evidence/v3-rollout/README.md`；网关主题与会话页的本地与生产验收见 `docs/evidence/alice-theme/README.md`。

## 4. 待用户决定

1. **分享视图的上游 paseo 根治（可选）**：首次加载落到 `/open-project` 与重复连接卡住时间线，目前由网关注入的 `paseo-boot.js` 绕开（ARCHITECTURE §8，已验收）。根治需要给 `mouriya-s-lab/paseo` 提 PR：带上旧 fork `6a59f442` 的「不重复探测已持久化的自托管连接」，并让 host 路由等 manifest 探测结束再判断；合入并升级基础镜像后可删除启动脚本。
2. **needs_human 的分享链接指向已归档的 agent**：进入 human 状态时调度会归档上一个 agent，链接打开后是 `This agent is archived`，时间线为空。重复连接问题已由启动脚本绕开，下一个 needs_human 会话出现时再核对时间线是否因归档而为空。如果需要人能在链接里看到 AI 的过程，契约需要调整：human 状态保留上一个 agent，到结束时再归档。
3. **`paseo-alicedev` fork**（本机 `~/Ext/code/paseo-alicedev`，分支 `feat/arch-image` `feat/embed-mode` `feat/upgrade-bot-conductor`）已不再使用；是否删除分支或整个仓库由用户决定。
4. **图片卡片是否改用 Alice 主题**：`templates/cards/` 仍是原来的蓝白样式；如果推广，从 `gateway/static/alice/alice.css` 取 token（ARCHITECTURE §8）。

## 5. 已知缺口

- approve 的回话已改为 `state.deploying`（「%n 已批准，开始 merge 并部署。」）。这句新文字只有单测覆盖；approve → merge → 部署 → `active` 这条链路本身，是在改动之前用旧文字真实跑通的。下一次真实 `/升级bot` 批准时顺带确认。
- AstrBot 主动发送拿不到平台消息 id，引用 bot 消息时按首行 `%n` 标记找会话（ARCHITECTURE §6）。
- paseo 0.8.0 对 idle agent 执行 `stop` 是空操作，12h 空闲关闭只记在 `agents.status`。
- 本机遗留的旧 worktree（`~/Ext/code/alicedev-wt/*`、`/private/tmp/alicedev-driver-*`）早于本轮，未动。

## 6. 参考

- 主机：nekoringo2 `160.191.41.242`（netbird `100.85.238.88`）；nekoringo1 `218.33.108.254`（Komodo Core）。SSH key `~/.ssh/dev-dai`。
- 基础设施仓库 `~/Ext/code/nekoringo-iac`（`apps/alicedev` 只落 `deploy/` 与 `.env`，边界见其 README）。
- paseo daemon API 事实：`docs/research/paseo-api.md`；AstrBot 事实：`docs/research/astrbot-api.md`。
