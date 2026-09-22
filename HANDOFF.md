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
- **回滚物**：`/srv/alicedev/backups/pre-v3-<ts>/`（v3 前的 DuckDB、`paseo_home`、旧 `bot/` `deploy/` `templates/`、镜像 ID）；镜像标签 `alicedev/{paseo,gateway}:rollback-pre-v3`、`alicedev/{paseo,astrbot,gateway}:rollback-pre-a49ad63`、`alicedev/paseo:rollback-pre-f7ef76f`。

验收覆盖面与上线中修复的问题见 `docs/evidence/v3-rollout/README.md`。

## 4. 待用户决定

1. **分享视图的两个上游 paseo 前端问题**（paseo 源码不能改，网关侧的补丁方案此前已排除）：
   - 首次打开分享链接时，host 还没注册就解析路由，落到 `/open-project`；再打开一次同一个工作区地址才正常。
   - 自托管连接被重复探测，时间线停在 `Subscription released` 或是空的。`paseo-alicedev` fork 的 `6a59f442` 修过这个问题，去 fork 后问题又回来了。
   - 可选做法：给 `mouriya-s-lab/paseo` 提上游修复后升级基础镜像，或者接受现状。
2. **needs_human 的分享链接指向已归档的 agent**：进入 human 状态时调度会归档上一个 agent，链接打开后是 `This agent is archived`，时间线为空。时间线为空是归档造成的还是上面的连接问题造成的，还没分开验证。如果需要人能在链接里看到 AI 的过程，契约需要调整：human 状态保留上一个 agent，到结束时再归档。
3. **`paseo-alicedev` fork**（本机 `~/Ext/code/paseo-alicedev`，分支 `feat/arch-image` `feat/embed-mode` `feat/upgrade-bot-conductor`）已不再使用；是否删除分支或整个仓库由用户决定。

## 5. 已知缺口

- approve 的回话已改为 `state.deploying`（「%n 已批准，开始 merge 并部署。」）。这句新文字只有单测覆盖；approve → merge → 部署 → `active` 这条链路本身，是在改动之前用旧文字真实跑通的。下一次真实 `/升级bot` 批准时顺带确认。
- AstrBot 主动发送拿不到平台消息 id，引用 bot 消息时按首行 `%n` 标记找会话（ARCHITECTURE §6）。
- paseo 0.8.0 对 idle agent 执行 `stop` 是空操作，12h 空闲关闭只记在 `agents.status`。
- 本机遗留的旧 worktree（`~/Ext/code/alicedev-wt/*`、`/private/tmp/alicedev-driver-*`）早于本轮，未动。

## 6. 参考

- 主机：nekoringo2 `160.191.41.242`（netbird `100.85.238.88`）；nekoringo1 `218.33.108.254`（Komodo Core）。SSH key `~/.ssh/dev-dai`。
- 基础设施仓库 `~/Ext/code/nekoringo-iac`（`apps/alicedev` 只落 `deploy/` 与 `.env`，边界见其 README）。
- paseo daemon API 事实：`docs/research/paseo-api.md`；AstrBot 事实：`docs/research/astrbot-api.md`。
