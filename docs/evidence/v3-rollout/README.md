# v3 上线验收（nekoringo2，2026-09-23）

生产 main = `1198e20`（`/v1/health` 的 `revision` 与宿主检出一致）。所有 TG 步骤都走真实路径：本机账号经服务器上的 `alicedev-tg-cli` 私聊 `@ririOuObot`，用 `tg recent` 取回 bot 的消息，图片用本机 Telethon 会话下载。

## 普通会话链路（Telegram）

| 步骤 | 观察 | 证据 |
|---|---|---|
| `/alicedev` | 帮助卡（指令、场景）出图 | `01-help.jpg` |
| `/需求 验收测试：…` | 回「已开始 %5 …」；AI 回复首行带 `%5 名称` 标记 | `02-requirement-ai-reply.jpg` |
| `/会话 %5` | 会话卡，含场景流程与当前状态 | `03-session-card.jpg` |
| `/需求列表` | 列出迁移来的旧需求（%1 %3）与新会话 | `04-requirement-list.jpg` |
| 自然消息、`％5`（全角）、`/切换` `/重命名` `/收藏` `/收藏夹`、引用 AI 回复继续 | 均路由到对应会话；引用按首行 `%n` 标记找回会话 | TG |
| `/帮我调查 …` | 报告链接公开可打开 | `05-investigate-report.png` |
| 直接发 GitHub PR 链接 | 新开 %7（github-pr 场景） | TG |
| `/归档 %5` | 回「已归档 %5 验收会话」，列表状态为「已归档」 | `14-all-sessions-states.jpg` |
| `/sl 全部 2` | 第 2 页，页脚为完整指令 `/会话列表 全部 n` | `15-all-sessions-page2.jpg` |

## `/升级bot`（模型 `muse-spark-1.3-contributor`）

| 状态 | 触发 | 观察 |
|---|---|---|
| `working` → `awaiting_approval` | `/升级bot 给 /会话列表 增加别名 sl` | AI 建 issue #9、PR #10，跑 DSL check、pytest、e2e before/after；TG 收到文字 + 说明图 + 证据图 + 指令卡（`10`–`12`） |
| `deploying` → `active` | `/升级bot approve %8` | PR #10 merge 为 `80161d5`，`deployrun` 热重载，health revision = `80161d5`；`/sl` 在 TG 上返回会话列表（`13-sl-after-deploy.jpg`） |
| `main_sync_failed` | fixed-main 放一处已跟踪改动后 `/升级bot …` | 只回一条「%11 没能开始 … 需要人工清理」+ 一次性链接；恢复 fixed-main 后下一个升级正常派发 |
| `needs_human` | 需求超出 `bot/`、`templates/` 交付范围 | AI 说明原因，bot 在其后附一次性链接 |
| `needs_human` 上 `approve` | `/升级bot approve %12` | 「%12 现在不在等待批准的状态。」 |
| `rejected` | `/升级bot reject %13` | 只回「%13 已拒绝，会话结束。」 |
| `rolled_back` | e2e 检出上 `deployctl run` 一个让插件加载失败的提交 | `outcome: rolled_back`，切回上一 SHA 后 health 通过 |
| `deploy_failed` | 检出先停在坏提交，再 `run` 另一个坏提交 | `outcome: deploy_failed`，`rollback_error` 同样失败，exit 1；随后 `apply` 正常 SHA 恢复 |

`rolled_back` / `deploy_failed` 用的坏提交只存在于 paseo 容器内的临时本地仓库（`--source /tmp/badrepo`），没有进入 GitHub 或生产检出。

## DSL 校验（e2e 栈）

在 e2e 检出放入带未知键的 `templates/commands/坏指令.yaml`，重启 e2e AstrBot：日志 `DSL commands/坏指令.yaml:7 指令 有未知的键：bogus_key`，`alicedev initialized: 15 commands, 5 scenarios, 1 DSL errors`。WebChat 上 `/会话列表` 照常回复，`/alicedev 坏指令` 回「没有这条指令」（`20-e2e-dsl-injection.png`）。

e2e before/after（`3a50eca` 对 `b6e817a`，`/alicedev` + `/会话列表`）：两侧 F=[]、E=[]、d=0，帮助卡图片与文字回话都在截图里（`21`、`22`）。

## 分享视图

网关注入 `paseo-boot.js` 后（`ceb4ba3`），在全新的 agent-browser 会话里从 TG 签发的 `/链接 %6` 进入：

| 步骤 | 地址 | 时间线 | 发消息 |
|---|---|---|---|
| 首次打开（`30-share-first-open.png`） | 停在 `/h/<server>/workspace/<workspace>`，左侧栏不可见 | 加载，无 `Updating messages` | 40 秒收到回复 |
| 刷新（`31-share-after-reload.png`） | 同上 | 同上 | 28 秒收到回复 |
| 后退到中转页再前进（`32-share-after-back-forward.png`） | 同上 | 同上 | 96 秒收到回复 |

注入前的对照：首次打开落到 `/open-project`；本地已有持久化 host 时刷新，`Updating messages` 持续 20 秒以上不消失。

在 paseo 界面里直接发的消息不是群聊入站，AI 的 `chat_reply` 返回 `no_pending_messages`，回答只留在 paseo 里，这是 ARCHITECTURE §5 的设计。

## 上线过程中发现并修复的问题

| 提交 | 问题 |
|---|---|
| `949c587` | main_sync_failed 的状态消息之外又回一条「创建失败」 |
| `2a657fa` | AstrBot 重载只清 `data.plugins.alicedev*`，插件自带的 `alicedev` 包不重载，Python 改动热重载不生效 |
| `a49ad63` | 人工指令回两条（状态文字 + `已处理：<状态>`）；approve 进入 `deploying` 时没发 `state.deploying` |
| `f95a7e2` | paseo 镜像登录 shell 找不到 bot 依赖；以 root 运行 e2e_driver 留下 root 所有的 git 文件 |
| `6fe32ac` | bot 以 root 执行 paseo CLI，生成 paseo 用户读不了的 `~/.paseo/cli-client-id` |
| `a854392`、`69fde2c`、`6ddf963` | e2e_driver 无法加载 deployctl；登录依赖标签与固定等待；只看流式回复，主动消息（全部 bot 回话）看不到，before/after 两侧都是空气泡；新配置落到欢迎页 |
| `b6e817a` | e2e 卷里 `t2i_endpoint` 为空，卡片全部渲染失败 |
| `1077dad` | 分享视图首次加载落到 `/open-project` 时整页空白 |
| `1198e20` | `/会话列表 全部` 的页脚显示 `/全部 n` |
| `ceb4ba3` | 分享视图：首次打开落到 `/open-project`，刷新后时间线卡在 `Updating messages`（上游 paseo 前端问题，由网关注入的启动脚本绕开） |
