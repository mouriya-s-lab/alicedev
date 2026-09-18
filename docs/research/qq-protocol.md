# QQ 协议端选型事实（AstrBot / Linux 容器）

> 研究快照：2026-09-19；GitHub/文档页面主要显示到 2026-09-18。目标是“开发者社区群机器人尽量长期在线”，不是规避 QQ 的风控或封禁。QQ 的风控、设备识别和策略会变化；任何非官方个人账号协议端都不能承诺不掉线/不封号。
>
> **结论先行：** 若能接受 QQ 开放平台的群机器人审核、权限和消息范围，首选 **QQ 官方机器人 API + AstrBot 的 `qq_official` 适配器**：没有个人 QQ 账号登录、NTQQ 注入或第三方签名服务器，账号被踢/封的风险模型完全不同。若必须使用普通个人 QQ 号、必须接 OneBot v11/`aiocqhttp`，则选择 **NapCat（NTQQ/Linux QQ）作为“最少额外迁移成本”的条件性 fallback**，并用持久化设备数据、保守发送、监控和人工介入；这不是安全保证。

## 1. 候选事实

### 1.1 NapCat（NapNeko/NapCatQQ）

| 项目 | 事实与判断 |
|---|---|
| 维护状态（日期） | `NapNeko/NapCatQQ` 未归档，仓库页面 2026-09-18 仍更新；最新 release **v4.18.28，2026-09-14**。上游 README 将其描述为基于 NTQQ 的现代 Bot 协议端，并声明持续维护（维护声明不是在线/封禁保证）。[NapCat README](https://github.com/NapNeko/NapCatQQ) · [releases](https://github.com/NapNeko/NapCatQQ/releases) |
| 协议基础 | **NTQQ / Linux QQ 客户端**：NapCat README 明确写 “based on NTQQ”；Linux Docker 镜像运行 QQNT，并通过 NapCat 注入/适配提供 Bot API。它不是重新实现 Mirai/Android 协议。 |
| Docker / 镜像 | 上游 Docker 仓库为 [`NapNeko/NapCat-Docker`](https://github.com/NapNeko/NapCat-Docker)，其 Compose 使用 `mlikiowa/napcat-docker:latest`，支持 Linux amd64/arm64，数据目录 `/app/.config/QQ`、配置 `/app/napcat/config`。这是与上游仓库配套的 Docker Hub 镜像，但镜像命名空间不是 `NapNeko`；生产环境应在拉取后记录 digest，不应盲信 `latest`。 |
| 登录 | WebUI 默认 `:6099/webui` 扫码/快速登录；当前后端也暴露 QR、密码 MD5、验证码和新设备验证流程（密码登录可能被 QQ 要求验证码/新设备确认）。首次扫码后持久化 QQ 数据可支持快速登录；不要把 QQ 明文密码写入 Compose。证据：[NapCat-Docker README](https://github.com/NapNeko/NapCat-Docker) · [QQLogin.ts](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-webui-backend/src/api/QQLogin.ts) |
| 外部 sign server | 标准 NapCat Docker/NTQQ 路径**没有上游文档要求配置 qsign/外部 sign server**；不要把 NapCat 的 OneBot access token、WebUI token 与 QQ 协议签名混淆。当前仓库代码搜索未发现 `qsign` 配置；这只能证明“当前公开配置路径未见该依赖”，不能证明未来 QQ 版本永远不需要额外服务。 |
| OneBot v11 / AstrBot | 支持 OneBot v11，且 AstrBot 官方 `aiocqhttp` 文档直接列 NapCat 并要求 **反向 WebSocket**；AstrBot 作 server，NapCat 作 client，URL 约定 `ws(s)://<host>:6199/ws`。[AstrBot aiocqhttp docs](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/en/platform/aiocqhttp.md) |
| 资源 / GPU | 上游没有正式 CPU/RAM 最低值。Linux Docker 通过虚拟显示（Xvfb/Xorg）运行 Electron/QQ；官方安装说明使用 `xvfb-run`，没有 `/dev/dri`、NVIDIA、VA-API 或 SR-IOV 要求。[NapCat Linux 启动说明](https://napneko.github.io/guide/boot/Shell-Linux-SemiAuto) · [Docker repo](https://github.com/NapNeko/NapCat-Docker) 。**[INFERENCE]** 单账号可先按 2 vCPU、2–4 GiB RAM 预算并实测，GPU 不应作为稳定性措施；iGPU 可能只改变 Electron 渲染 CPU 占用，不会降低 QQ 风控概率。 |
| 掉线/封禁证据 | 风险是明确的、且有 2026 一手 issue：[#1728](https://github.com/NapNeko/NapCatQQ/issues/1728)（2026-03-25，QQNT 3.2.25 + NapCat 4.17.53，数小时后反复 `[KickedOffline]`/风控；评论有旧版本短期稳定的个案，也有仍触发风控的个案）；[#1796](https://github.com/NapNeko/NapCatQQ/issues/1796)（2026-04-24，Docker 与 Linux/aarch64，频繁或静默下线，日志显示 `[KickedOffLine]`）；[#2027](https://github.com/NapNeko/NapCatQQ/issues/2027)（2026-08-20，掉线后无法再次登录，需重启，评论提到一小时/一天多次）。这些是用户报告/issue，不是可泛化的封禁概率；但足以否定“稳定不掉线”的承诺。 |

**NapCat 风险判断：** NTQQ 基础使其比 go-cqhttp 的旧协议路线更有维护前景，AstrBot 有现成连接文档；但 2026 仍有 QQ 风控/踢下线报告。选择它是工程兼容性折中，不是安全结论。

### 1.2 LLOneBot / LuckyLilliaBot（LLBot）

| 项目 | 事实与判断 |
|---|---|
| 维护状态（日期） | 现行仓库是 [`LLOneBot/LuckyLilliaBot`](https://github.com/LLOneBot/LuckyLilliaBot)（常简称 LLOneBot/LLBot），未归档，页面 2026-09-18 更新；最新 release **v8.2.1，2026-09-15**。README 明确支持 OneBot 11、Satori、Milky。[README](https://github.com/LLOneBot/LLOneBot) · [releases](https://github.com/LLOneBot/LuckyLilliaBot/releases) |
| 协议基础 | 两条模式：**直连**是代码协议实现、较省内存；**PMHQ 有头模式**在单独容器运行真实 Linux QQ/QQNT 客户端，由 LLBot 连接 PMHQ，文档称有头模式更稳但更耗资源。两者都不是 QQ 官方 Bot API。 |
| Docker / 镜像 | 官方 Docker 文档使用 `linyuchen/llbot`；PMHQ 模式还需要 `linyuchen/pmhq`。文档说明发布镜像为 Alpine/musl，v8.0.8+ 包含 `sign-proxy` 的 musl 二进制；镜像约 540 MB（文档对比 Debian 约 880 MB）。[Docker docs](https://github.com/LLOneBot/LuckyLilliaBot/blob/main/docs/docker.md) 。`latest`/标签可能漂移或缺失，曾有安装脚本生成不存在标签的 [#798](https://github.com/LLOneBot/LuckyLilliaBot/issues/798)；应先 `docker manifest inspect` 并固定 digest。 |
| 登录 | WebUI/终端扫码、快速登录；`AUTO_LOGIN_QQ` 选择要恢复的 QQ，不是 QQ 密码。持久化数据后可快速登录；当前公开 Docker 文档没有可靠的 `QQ_PASSWORD` 明文环境变量路径。PMHQ 的 `quick_login_qq`/`AUTO_LOGIN_QQ` 可能因登录态失效而失败，见 [#769](https://github.com/LLOneBot/LuckyLilliaBot/issues/769)。 |
| 外部 sign server | 现行 Docker 方案将 `sign-proxy` 随 LLBot 包提供；公开配置页未给出 NapCat/go-cqhttp 式公共 qsign URL。PMHQ 可能需要其自身的 `auth_token`/授权服务（不是 QQ sign server），且日志中的授权服务超时会导致 Bot offline；不要把 PMHQ token 当作 QQ 签名服务。避免第三方公共 sign server。 |
| OneBot v11 / AstrBot | README 明确 OneBot 11；官方开发文档给出 `ws-reverse`，可接 AstrBot 的反向 WS。常见端点是 LLBot `3000` HTTP、`3001` WS，实际以当前 `default_config.json`/WebUI 为准；AstrBot 端仍按其 OneBot v11 reverse WS 配置。 |
| 资源 / GPU | 直连模式较轻；PMHQ 是额外的 Electron/QQ 容器，使用 Xvfb，Docker 日志/文档显示 `DISPLAY=:99` 与 `LIBGL_ALWAYS_SOFTWARE=1`，没有 SR-IOV/iGPU 必要条件。**[INFERENCE]** 直连可先预留 1–2 vCPU/1–2 GiB；PMHQ 至少另预留 2 vCPU/2–4 GiB，并以实际 RSS 为准。异常账号可能出现严重泄漏：[#792](https://github.com/LLOneBot/LuckyLilliaBot/issues/792) 报告约 100 MB 上升到 4 GB。 |
| 掉线/封禁证据 | [#857](https://github.com/LLOneBot/LuckyLilliaBot/issues/857)（2026-09-10，Docker、LLBot 8.1.10、QQ 3.2.28，反复掉线，扫码后仍不能登录，评论有人明确看到“风控/设备安全隐患”；截至 2026-09-17 仍有人报告无提示踢下线）；[#737](https://github.com/LLOneBot/LuckyLilliaBot/issues/737)（2026-04-16，非无头约一天清除登录信息）；[#534](https://github.com/LLOneBot/LuckyLilliaBot/issues/534)（2025-01-17 的社区风控/封号讨论，包含维护者关于账号风险的经验，但没有可重复的因果证明）。#857 中也有人换 NapCat/SnowLuma 仍掉线，说明部分现象可能是账号/IP/QQ 侧策略，而非 LLBot 单独导致。 |

**LLBot 风险判断：** 若特别重视“使用真实 QQ 客户端形态”，PMHQ 比直连更值得做小规模验证；但 2026-09 仍有开放的 Docker 掉线/风控报告，不能据此宣称优于 NapCat。PMHQ 的额外授权服务和内存异常也是运维风险。

### 1.3 Lagrange.Core / Lagrange.OneBot

| 项目 | 事实与判断 |
|---|---|
| 维护状态（日期） | `LagrangeV2` 已归档（GitHub 记录 2026-07-23）；活动开发迁回 [`LagrangeDev/Lagrange.Core`](https://github.com/LagrangeDev/Lagrange.Core)，其 README 说当前分支是 V2，V1 已 sunset。Lagrange.Core release 页面最新为 **nightly，2026-08-02**；稳定 NuGet `Lagrange.Core 2.1.1` 更新于 **2026-07-28**（以包管理器为准）。**Lagrange.OneBot V1 是 legacy/不再维护的 OneBot 11 适配器**；V2 官方方向为 Lagrange.Milky，而不是 OneBot 11。[Lagrange.Core README](https://github.com/LagrangeDev/Lagrange.Core) · [V2 docs](https://lagrangedev.github.io/Lagrange.Doc/v2/) · [V1 OneBot docs](https://lagrangedev.github.io/Lagrange.Doc/v1/Lagrange.OneBot/) |
| 协议基础 | NTQQ 协议的纯 C# 实现；V1 `Lagrange.OneBot` 供 OneBot 11，V2 `Lagrange.Milky` 供 Milky。不能把当前 Core V2 与旧 OneBot 镜像当作同一个维护目标。 |
| Docker / 镜像 | 旧 V1 OneBot 有官方 GHCR 包 `ghcr.io/lagrangedev/lagrange.onebot:edge`（也可见按 commit 的镜像），但它属于 legacy OneBot 路线；不要因 GHCR 包仍存在就认为 V1 仍获官方维护。V2/Milky 应按其当前文档/自建镜像部署。 |
| 登录 | V1 官方配置以扫码为主：`Account.Uin`，`Password` 留空；当前 V1 文档称密码字段不再支持，首次运行生成二维码文件。不要把 QQ 明文密码放进 `appsettings.json`。V2 Sign API 文档也以 `Password: null` 表示 QR 登录。 |
| 外部 sign server | **V1 Linux/NTQQ 路径通常需要兼容的 NTQQ SignServer**：官方示例有 `SignServerUrl`，且要求签名版本匹配 QQ 协议版本；明确警告不要拿 Android `unidbg-fetch-qsign` 充当 NTQQ 签名服务。V2/Milky 使用 `Signer.Url` + token 的新格式；两种配置不可混用。[V1 配置](https://lagrangedev.github.io/Lagrange.Doc/v1/Lagrange.OneBot/Config/) · [SignApiGuide](https://github.com/LagrangeDev/SignApiGuide) |
| OneBot v11 / AstrBot | 旧 V1 OneBot 支持 ReverseWebSocket（官方示例 `Suffix: /onebot/v11/ws`），因此协议上可接 AstrBot；但由于 V1 sunset，长期维护风险明显高于 NapCat/LLBot。V2 Milky 不应假设可直接接 AstrBot `aiocqhttp`。 |
| 资源 / GPU | 纯 .NET/协议进程，官方没有 CPU/RAM 最低值，也没有 GPU 要求；**[INFERENCE]** 通常比完整 QQNT/Electron 轻，CPU-only 即可，但这不抵消 V1/签名服务维护风险。 |
| 掉线/封禁证据 | [#746](https://github.com/LagrangeDev/Lagrange.Core/issues/746)（2025-01-23，V1 OneBot Linux，扫码成功后收到 `KickNTEvent`“账号已在另一台终端登录”，多名用户报告删除 `device.json`/`keystore.json`或等待后恢复；更像设备/会话冲突，不能直接判定封号）。[#459](https://github.com/LagrangeDev/Lagrange.Core/issues/459)（2024-07-26，Code 45/“禁止登录”，评论指向过期签名地址，说明签名故障与封号不能混为一谈）。截至本快照，未找到足够的 2026 Lagrange.OneBot 新报告来证明它比 NapCat/LLBot 更稳；“未找到”不是“安全”。 |

**Lagrange 风险判断：** 对只接受 OneBot v11 的 AstrBot 新部署，不建议把已 sunset 的 V1 作为长期首选；它还引入协议版本绑定的 sign server 运维面。

### 1.4 go-cqhttp

| 项目 | 事实与判断 |
|---|---|
| 维护状态（日期） | 最新正式 release **v1.2.0，2023-10-09**。仓库 README 明确写因 QQ 协议/加密持续变化已无力维护并建议迁移到无头 NTQQ；维护者迁移公告 [#2471](https://github.com/Mrs4s/go-cqhttp/issues/2471) 仍是最关键状态证据（issue 页面 2026-08-27 仍有更新，但不是代码恢复维护）。 |
| 协议基础 | Mirai/MiraiGo 逆向协议实现，非 NTQQ、非官方 API。 |
| Docker / 镜像 | 官方 GHCR：`ghcr.io/mrs4s/go-cqhttp:1.2.0`（文档也有 `master/latest`，但不应生产使用浮动标签）。[Docker 文档](https://docs.go-cqhttp.org/guide/docker.html) · [GHCR package](https://github.com/Mrs4s/go-cqhttp/pkgs/container/go-cqhttp) |
| 登录 | `password: ''` 走扫码；也支持密码字段（可配置加密），滑块/设备验证仍可能出现。持久化 `/data` 中 `config.yml`、`device.json`。 |
| 外部 sign server | **依赖/常需要外部 sign server**：`account.sign-servers` 的 URL、key、authorization 等配置，且需与协议版本匹配。官方文档提醒公共 sign server 的可用性/隐私风险；sign server 不是防封服务。 |
| OneBot v11 / AstrBot | README 列 HTTP API、反向 HTTP POST、正向 WS、反向 WS，兼容 OneBot v11 大部分内容；可以接 AstrBot，但 go-cqhttp 维护者自己建议迁移。 |
| 资源 / GPU | Go 进程，无 GPU。README 的特定基准：关闭数据库、25 好友/128 群、24h 约 15 MB；数据库再增加约 10–20 MB，并建议小于 128 MB 时关闭数据库。这不是现代稳定部署的保证；GPU 不相关。 |
| 掉线/封禁证据 | 没有可比的 2025–2026 新 issue 证据；但这不是好消息，而是项目已基本停止维护。[#2471](https://github.com/Mrs4s/go-cqhttp/issues/2471) 的维护者明确说明 sign-server 路线可能被官方彻底封死、建议迁移；历史 [sign server discussion #2245](https://github.com/Mrs4s/go-cqhttp/discussions/2245) 与 Code 45/限制登录 issue 说明签名失败、登录限制、消息风控彼此相关但不等价。 |

**go-cqhttp 风险判断：** 不应作为 2026 新部署方案；低内存和成熟 OneBot 生态不能弥补无维护、旧协议和 sign-server 依赖。

### 1.5 QQ 官方机器人 API（QQ 开放平台）

| 项目 | 事实与判断 |
|---|---|
| 维护状态（日期） | 这是 QQ 官方平台而非个人账号协议端。官方 Node SDK [`tencent-connect/qqbot-nodejs`](https://github.com/tencent-connect/qqbot-nodejs) release **1.0.4，2026-07-31**；README 支持 WebSocket Gateway、Webhook、自动 token 管理、心跳/RESUME、媒体和 TypeScript。Python 官方 SDK [`qqbot-agent-sdk`](https://github.com/tencent-connect/qqbot-agent-sdk) 也提供 Gateway/OpenAPI。 |
| 协议基础 | **官方 QQ Bot Open Platform API**；AppID/AppSecret 换 access token，再通过官方 Gateway WebSocket 或 Webhook 收发。不是登录个人 QQ，不需要 QQNT、注入或个人 QQ device.json。 |
| Docker / 镜像 | 腾讯 SDK 仓库没有官方运行时 Docker 镜像；通常在自己的 Node/Python 镜像中安装 SDK。对本项目无需额外 SDK 容器：AstrBot 自带 `qq_official` 适配器，直接运行 `soulter/astrbot:latest` 或自建 AstrBot 镜像即可。 |
| 登录 | 没有“QQ 个人账号 QR/密码登录”。AstrBot 官方 QQ Bot 文档支持 **One-click QR setup**：手机 QQ 扫码绑定开放平台凭据；也可在开放平台拿 AppID/AppSecret。这个 QR 是 Bot 应用绑定，不是把个人 QQ 登录到服务器。 |
| 外部 sign server | **不需要**。使用官方 access token/Gateway；没有 qsign/NTQQ 签名服务。 |
| OneBot v11 / AstrBot | 不走 OneBot v11/`aiocqhttp`，直接选择 AstrBot 的 `qq_official` 平台适配器。AstrBot 文档推荐该路径，理由是官方提供、稳定且支持扫码初始化。 |
| 资源 / GPU | 只运行官方 API WebSocket/Webhook 客户端和 AstrBot；无 QQ Electron/Xvfb，官方 SDK 为 Node/Python 网络客户端，**不需要 GPU**。官方未给最低 CPU/RAM；额外进程开销应远小于完整 Linux QQ 客户端（这是工程常识级 [INFERENCE]，应在目标镜像中实测）。 |
| 风险 | 没有个人 QQ 客户端被踢/设备风控这条风险链；风险转为 App 审核、IP 白名单、权限、群范围、官方频控、内容合规和平台停用。官方文档要求开放平台创建/审核/上线、IP 白名单；群主动消息有频控（以当前官方 API 页面为准）。这不是“任何内容都不会被限制”的保证，而是官方账号模型。 |
| 能力边界 | 需要群主/管理员添加机器人，且受沙箱/审核/白名单约束；群消息通常需要在后台开启相应访问范围，普通 QQ 号、历史 QQ 群号、任意私聊能力不能照搬 OneBot。若需求只需开发者社区群中 @机器人/回复/主动通知，这些限制通常可接受。 |

官方入口：[QQ Bot 文档](https://bot.q.qq.com/wiki/) · [API v2](https://bot.q.qq.com/wiki/develop/api-v2/) · [AstrBot QQ WebSocket](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/en/platform/qqofficial/websockets.md) · [AstrBot 当前文档说明 `appid`/`secret`](https://docs-v4.astrbot.app/en/platform/qqofficial/websockets.html)

## 2. 比较表与推荐

### 2.1 快速比较

| 候选 | 维护（截至快照） | 协议/账号 | Docker | QR/密码 | 外部 sign server | OneBot v11 / AstrBot | GPU | 风险结论 |
|---|---|---|---|---|---|---|---|---|
| **QQ 官方 Bot API** | 官方平台；Node SDK 1.0.4 (2026-07-31) | 官方 Bot 应用 | 无官方运行时镜像；自建 SDK 镜像或 AstrBot 镜像 | Bot 绑定 QR；非个人 QQ 密码 | 否 | 不走 aiocqhttp；用 `qq_official` | 否 | **首选（若接受审核/权限）**；没有个人账号踢下线链 |
| **NapCat** | v4.18.28 (2026-09-14)，活跃 | NTQQ/Linux QQ | `mlikiowa/napcat-docker:latest`（配套社区镜像） | WebUI QR、快速登录、密码/验证码路径 | 标准路径未要求外部 qsign | **是**，AstrBot 官方有反向 WS 指引 | 不需要；Xvfb | **OneBot fallback 首选**；2026 有反复风控/踢线报告 |
| **LLBot** | v8.2.1 (2026-09-15)，活跃 | 直连协议或 PMHQ/真实 QQ | `linyuchen/llbot`; PMHQ `linyuchen/pmhq` | WebUI/CLI QR、快速登录 | bundled sign-proxy；PMHQ 另有 auth token 可能 | **是** | 不需要；PMHQ Xvfb/software rendering | PMHQ 可试稳定性，但 2026 Docker 掉线 issue 仍开放 |
| **Lagrange.OneBot** | V1 sunset；Core V2 nightly (2026-08-02) | NTQQ 纯 C#；V2 方向 Milky | legacy GHCR `ghcr.io/lagrangedev/lagrange.onebot:edge` | V1 以 QR 为主；密码不应依赖 | **通常需要匹配 NTQQ SignServer** | V1 是；V2/Milky 不是 aiocqhttp | 否 | 不建议新 AstrBot 长期部署；V1/签名服务维护面大 |
| **go-cqhttp** | v1.2.0 (2023-10-09)，停止维护 | Mirai/MiraiGo 逆向协议 | `ghcr.io/mrs4s/go-cqhttp:1.2.0` | QR 或密码 | 常需要 qsign/sign server | **是** | 否 | **排除**：过时、维护者要求迁移 |

### 2.2 给 alicedev 的决定

1. **默认方案：QQ 官方 Bot API + AstrBot `qq_official`。** 目标是开发者社区群，不是兼容任意个人号；官方路径最直接地满足“尽量不因协议端被踢/封”。在开放平台申请/审核、配置 IP 白名单、让群主添加 Bot，并开启需要的群消息范围。
2. **Fallback：若业务必须使用个人 QQ 或目标群无法添加官方 Bot，选 NapCat Docker + AstrBot reverse WS。** 它是 AstrBot 文档直接覆盖的 NTQQ/OneBot 方案，迁移成本低于自接 Lagrange V2/Milky。必须把“个人账号有非零风控风险”写入运行手册和账号预算；推荐先单账号低频运行，不把 iGPU 作为风控解决方案。
3. **LLBot PMHQ 仅作为第二个实验分支。** 若 NapCat 的具体 QQNT/环境组合掉线，可在隔离账号上比较 LLBot PMHQ；不要同时运行同一 QQ 的 NapCat、LLBot、Lagrange 或官方 QQ 客户端来“测试稳定性”，否则会产生互踢/设备信号。
4. **不选 Lagrange.OneBot V1 作为新长期基线，不选 go-cqhttp。** 前者 OneBot 路线 sunset 且依赖匹配签名；后者维护者明确宣布无法继续维护。

### 2.3 证据冲突与不确定性

- 社区“旧版本更稳”与“升级后仍风控”同时存在：NapCat #1728 有单账号 4.15.19 运行 63h 的个案，也有同线程 2026-09-17 评论称该旧 QQ 版本已被禁止登录。不能把个案转成推荐版本或概率。
- LLBot #857 中有人换 NapCat/SnowLuma 后仍被踢；这支持“QQ 账号/IP/策略也可能是主因”，但不能证明所有 LLBot/NapCat 都同样不稳定。
- `KickedOffline`、`-10003`、Code 45、`QQ 版本过低`、登录态失效、签名服务超时不是同一个错误。排障时必须保留 QQ/协议端版本、登录模式、时间、完整日志和 QQ 安全中心提示，不能一概叫“封号”。
- “没有 issue”只代表没有公开证据，不代表安全；不同账号年龄、历史设备、IP、行为量和 QQ 风控命中状态不可比较。

## 3. Compose 与 AstrBot 配置

### 3.1 首选官方 API：只需 AstrBot（无 QQ 协议容器）

官方 API 不需要 NapCat/Lagrange/go-cqhttp 容器，也没有反向 WS 端口。下面是部署形态；`QQ_OFFICIAL_APPID`/`QQ_OFFICIAL_SECRET` 仅作为 secret 注入占位，**当前 AstrBot 最可靠的配置入口是 WebUI/平台配置，不应假设任意环境变量会自动写入平台配置**：

```yaml
services:
  astrbot:
    image: soulter/astrbot:latest
    container_name: astrbot
    restart: unless-stopped
    init: true
    environment:
      TZ: Asia/Shanghai
      # 仅供部署系统/人工配置时使用；不要提交真实值。
      QQ_OFFICIAL_APPID: ${QQ_OFFICIAL_APPID:?set in secret store}
      QQ_OFFICIAL_SECRET: ${QQ_OFFICIAL_SECRET:?set in secret store}
    ports:
      - "127.0.0.1:6185:6185"
    volumes:
      - ./astrbot-data:/AstrBot/data
```

在 AstrBot WebUI 的 Platforms/Bots → Add Adapter → **QQ Official Bot (WebSocket)** 配置：

```json
{
  "id": "qq_official",
  "type": "qq_official",
  "enable": true,
  "appid": "<从 QQ 开放平台或一键 QR 绑定得到>",
  "secret": "<从 QQ 开放平台得到>",
  "enable_group_c2c": true,
  "enable_guild_direct_message": false
}
```

- 更推荐 AstrBot 的 **One-click QR setup**，由手机 QQ 扫码绑定后让 AstrBot 自动填写 `appid`/`secret`；不把 secret 写入仓库。
- 开放平台配置服务器公网 IP 白名单；发布/上线和群主添加是官方流程。
- 该路径没有 `ws://astrbot:6199/ws`，因为 AstrBot 自己连接 QQ 官方 Gateway；不要给官方 API 额外套 OneBot。
- 官方 Node SDK 自建容器时可用 `@tencent-connect/qqbot-nodejs`（Node >=20），但 alicedev 不需要另写 SDK bridge，除非 AstrBot 适配器无法覆盖所需 API。

### 3.2 OneBot fallback：NapCat + AstrBot reverse WebSocket

此 Compose 采用 [NapCat-Docker 上游 AstrBot 模板](https://github.com/NapNeko/NapCat-Docker/blob/main/compose/astrbot.yml) 的镜像、环境变量和卷路径，并把 AstrBot reverse WS 端口绑定为仅本机可访问。两个服务在同一 Compose 网络，NapCat 连接 `astrbot` 服务名，**不能在 NapCat 容器内用 `127.0.0.1` 连接 AstrBot**。

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:latest
    container_name: napcat
    restart: always
    environment:
      NAPCAT_UID: "${NAPCAT_UID:-1000}"
      NAPCAT_GID: "${NAPCAT_GID:-1000}"
      MODE: astrbot
      TZ: Asia/Shanghai
      # 可选：仅指定已有快速登录账号；不要在此写 QQ 明文密码。
      # NAPCAT_QUICK_ACCOUNT: "<QQ号>"
    ports:
      - "127.0.0.1:6099:6099" # WebUI；不要直接暴露公网
    volumes:
      - ./napcat/qq:/app/.config/QQ
      - ./napcat/config:/app/napcat/config
      - ./napcat/plugins:/app/napcat/plugins
    networks: [qqbot]

  astrbot:
    image: soulter/astrbot:latest
    container_name: astrbot
    restart: unless-stopped
    environment:
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:6185:6185" # AstrBot WebUI
      - "127.0.0.1:6199:6199" # OneBot reverse WS listener; no public exposure
    volumes:
      - ./astrbot-data:/AstrBot/data
    networks: [qqbot]

networks:
  qqbot:
    driver: bridge
```

首次运行：

```bash
NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g) docker compose up -d
# 浏览器仅从受信网络访问 http://127.0.0.1:6099/webui（或经 SSH 隧道）完成扫码
# docker logs napcat 可见 WebUI/token/登录状态提示
```

NapCat WebUI 中的 OneBot 11 网络配置（当前字段名可能随 NapCat 版本变化，优先用 WebUI 生成）：

```json
{
  "network": {
    "websocketClients": [
      {
        "name": "astrbot",
        "enable": true,
        "url": "ws://astrbot:6199/ws",
        "messagePostFormat": "array",
        "reportSelfMessage": false,
        "reconnectInterval": 5000,
        "token": "<与 AstrBot 完全相同的随机 token>",
        "heartInterval": 30000,
        "debug": false
      }
    ]
  }
}
```

AstrBot WebUI → Add Adapter → **OneBot v11**：

- `id`: 任意唯一值，例如 `qq_napcat`。
- `enable`: enabled。
- Reverse WebSocket host: `0.0.0.0`（容器内监听）。
- Reverse WebSocket port: `6199`。
- Reverse WebSocket token: 与 NapCat 配置相同；若不启用 token 则两边都为空，但生产建议随机 token。
- AstrBot 官方约定路径：`/ws`；成功日志为 `aiocqhttp(OneBot v11) adapter connected.`。

如果使用旧 NapCat WebUI 版本，其字段可能仍显示“WebSockets 客户端/反向 WS”，但语义相同：NapCat 主动连 `ws://astrbot:6199/ws`，AstrBot 监听 `0.0.0.0:6199`。不要把 `6199` 暴露到公网；只暴露 AstrBot/NapCat WebUI 到管理网络。

**为什么没有 GPU 配置：** NapCat Docker 官方模板没有 GPU 参数，Linux 启动依靠 Xvfb；先 CPU-only 运行。只有独立的 AI/OCR/视频工作负载明确需要 VA-API 时，才单独评估 `/dev/dri`；不要把 SR-IOV iGPU 当作防踢或反检测手段。

## 4. 运维：账号、掉线检测、重登与风险控制

### 4.1 账号与网络

- 首选官方 Bot；若使用个人 QQ，准备专用、可丢弃且有正常历史的账号，不要把主账号当实验账号。账号年龄/活跃度可能影响风险，但没有公开、可验证的安全阈值；不要把“老号”当保证。
- 固定一个长期服务器/IP/设备数据目录；不要频繁换 IP、容器 MAC、`machine-id`、QQ 数据卷或反复删除 `device.json`/会话文件。只有确认会话损坏或官方文档要求时才清理，并先备份。
- 同一 QQ 只运行一个协议端；不要并行启动 NapCat、LLBot、Lagrange、go-cqhttp 或桌面 QQ 进行“对比”，这会触发真实的互踢/设备登录事件。
- 限制群白名单、管理员、发送并发和主动通知；避免批量加群/加好友、短时间大量相似消息、重复失败重试、刷屏和高频 QR 扫码。这里是风险降低，不是规避检测。
- 不使用不明公共 sign server。go-cqhttp 官方文档提醒签名服务可能看到登录时间、QQ 号、消息/目标 ID 等运行元数据；第三方服务也增加失效和泄露面。

### 4.2 可观测的掉线信号

至少收集并告警：

1. NapCat/LLBot/Lagrange 日志中的 `KickedOffLine`、`KickNTEvent`、`登录已失效`、`账号异常`、`Code 45`、`-10003`、`QQ版本过低`、`禁止登录`、`sign failed`。
2. OneBot 连接层：AstrBot 出现 `aiocqhttp adapter has been closed`、反向 WS 断开、心跳超时；这只能证明协议连接断，不等于 QQ 账号被封。
3. 业务层：定时调用/观察 OneBot `get_status`/`get_login_info`（实现支持时），记录 `online`；群消息事件心跳长时间消失；发送 API 连续失败。
4. QQ 安全中心/手机 QQ 的设备风险提示、需要验证、社交功能限制、冻结或登录异常通知。
5. Docker health：容器仍然 `running` 不等于 QQ 在线；LLBot #792 证明账号异常时进程可能继续运行并占用大量内存。

建议把“协议进程在线”“OneBot WS 已连”“QQ `online=true`”“最近收到群事件”拆成四个状态，并在任一状态异常时通知 Telegram/管理员。

### 4.3 自动重登策略（保守）

- 容器层用 `restart: unless-stopped`/`always` 仅处理进程崩溃；不要用毫秒级无限重启。
- NapCat 当前实现监听 `KickedOffLine`，会清空失效二维码并异步重启 Worker；AstrBot/Compose 层仍应保留人工确认入口。NapCat issue #1258 说明部分版本掉线后需要重启容器才能恢复。
- 建议退避：第一次掉线等待 30–60 秒；第二次等待 5 分钟；连续 3 次在 30 分钟内发生则停止自动扫码/自动重启，通知管理员并暂停发送。**这是本项目的安全运营策略，不是 QQ 官方规定。**
- 自动重登只使用持久化的快速登录态；如果要求新设备验证、滑块、人脸或安全中心确认，停止自动化，人工用官方 QQ 完成验证。不要在失败时删除所有数据卷或循环扫新二维码。
- 重登成功判据不是进程退出码：必须同时看到 QQ online、OneBot reverse WS reconnect、AstrBot adapter connected，并在白名单测试群收到一条低频测试消息。
- 若同一账号多次被风控/冻结，停止协议端实验，保留脱敏日志，走 QQ 官方申诉/恢复流程或换账号；不要尝试用 GPU、随机设备指纹、公共签名服务或频繁换版本“绕过”风控。

## 证据来源（主要来源，均为可复核 URL）

1. [NapCatQQ README](https://github.com/NapNeko/NapCatQQ) — NTQQ 基础、OneBot 定位、维护状态。
2. [NapCat releases](https://github.com/NapNeko/NapCatQQ/releases) — v4.18.28 / 2026-09-14。
3. [NapCat-Docker README](https://github.com/NapNeko/NapCat-Docker) — `mlikiowa/napcat-docker`、amd64/arm64、卷路径、6099 WebUI。
4. [NapCat-Docker AstrBot compose](https://github.com/NapNeko/NapCat-Docker/blob/main/compose/astrbot.yml) — NapCat/AstrBot Docker 网络与环境变量模板。
5. [NapCat issue #1728](https://github.com/NapNeko/NapCatQQ/issues/1728) — 2026 风控/频繁 KickedOffline，含版本冲突与相反个案。
6. [NapCat issue #1796](https://github.com/NapNeko/NapCatQQ/issues/1796) — 2026 Docker/Linux 频繁或静默下线。
7. [NapCat issue #2027](https://github.com/NapNeko/NapCatQQ/issues/2027) — 2026 掉线后无法再次登录。
8. [AstrBot OneBot v11 docs](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/en/platform/aiocqhttp.md) — reverse WS、`0.0.0.0:6199`、`/ws`、token、连接日志。
9. [LuckyLilliaBot README](https://github.com/LLOneBot/LuckyLilliaBot) — OneBot 11/Satori/Milky。
10. [LuckyLilliaBot releases](https://github.com/LLOneBot/LuckyLilliaBot/releases) — v8.2.1 / 2026-09-15。
11. [LuckyLilliaBot Docker docs](https://github.com/LLOneBot/LuckyLilliaBot/blob/main/docs/docker.md) — Alpine/musl、直连与 PMHQ、`linyuchen/llbot`/`linyuchen/pmhq`。
12. [LuckyLilliaBot issue #857](https://github.com/LLOneBot/LuckyLilliaBot/issues/857) — 2026-09 Docker 反复掉线/风控报告。
13. [LuckyLilliaBot issue #737](https://github.com/LLOneBot/LuckyLilliaBot/issues/737) — 非无头约一天清除登录态。
14. [LuckyLilliaBot issue #792](https://github.com/LLOneBot/LuckyLilliaBot/issues/792) — 账号异常时幽灵进程和约 4 GB 内存。
15. [LuckyLilliaBot issue #769](https://github.com/LLOneBot/LuckyLilliaBot/issues/769) — Docker 快速登录失效、PMHQ Xvfb/software rendering 日志。
16. [Lagrange.Core README](https://github.com/LagrangeDev/Lagrange.Core) — V2 当前分支、V1 sunset、Milky 方向。
17. [Lagrange V1 OneBot config docs](https://lagrangedev.github.io/Lagrange.Doc/v1/Lagrange.OneBot/Config/) — QR、Reverse WS、NTQQ SignServer 与协议版本匹配。
18. [Lagrange V2 docs](https://lagrangedev.github.io/Lagrange.Doc/v2/) — V2 不以 OneBot 11 为目标，Milky 方向。
19. [Lagrange issue #746](https://github.com/LagrangeDev/Lagrange.Core/issues/746) — 2025 Linux OneBot `KickNTEvent`/设备会话冲突。
20. [Lagrange SignApiGuide](https://github.com/LagrangeDev/SignApiGuide) — V2 Signer URL/token 格式。
21. [go-cqhttp README](https://github.com/Mrs4s/go-cqhttp) — OneBot v11、协议/维护停止提示、性能基准。
22. [go-cqhttp migration issue #2471](https://github.com/Mrs4s/go-cqhttp/issues/2471) — 维护者关于 sign-server 路线与迁移 NTQQ 的声明。
23. [go-cqhttp Docker docs](https://docs.go-cqhttp.org/guide/docker.html) — 官方 GHCR 镜像路径。
24. [go-cqhttp config docs](https://docs.go-cqhttp.org/guide/config.html) — QR/密码、`account.sign-servers`、sign-server 风险。
25. [Tencent QQ Node SDK README](https://github.com/tencent-connect/qqbot-nodejs) — 官方 API WebSocket/Webhook、token/RESUME、Node >=20。
26. [Tencent QQ Node SDK releases](https://github.com/tencent-connect/qqbot-nodejs/releases) — v1.0.4 / 2026-07-31。
27. [Tencent QQ Python SDK](https://github.com/tencent-connect/qqbot-agent-sdk) — 官方 Gateway/OpenAPI、QR onboarding、会话恢复。
28. [QQ Bot official docs](https://bot.q.qq.com/wiki/) / [API v2](https://bot.q.qq.com/wiki/develop/api-v2/) — 官方 Bot 申请、权限、IP 白名单、群/消息 API。
29. [AstrBot QQ Official Bot docs](https://github.com/AstrBotDevs/AstrBot/blob/master/docs/en/platform/qqofficial/websockets.md) — `qq_official`、一键 QR、`appid`/`secret`、群配置与 IP 白名单。

> 本文是文档/issue 研究，不代表已在目标服务器登录 QQ、拉取镜像或运行 Compose；这些部署与账号行为仍需在隔离账号和受控网络中验证。