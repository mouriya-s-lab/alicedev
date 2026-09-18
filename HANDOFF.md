# alicedev — 交接文档

> 本文件是防 compact 遗忘的权威交接。任何新 session 先读本文件，再 `ctx list` 定位进度。

## 0. 工作方式约束（用户明确要求）

- 用户说完需求后由我自主 vibe 完成，遵照 system prompt 编排流程。
- 必须在本地留交接（本文件 + `docs/`），持续更新 `todo`，用 `ctx` 定位进度。
- 子代理：`task:mid` / `task:low` 用量无限（low 真正无限），均 Opus 级但**可能撒谎**；根因通常是任务太大。对策：切小、交叉验证、只认 artifact 与运行证据。`task:high` 为真 Opus，贵，仅用于必须自行定方案的切片。
- 架构必须可扩展，禁面条代码。重点：指令注册/分发、bot↔paseo 通讯层、渲染层、平台适配层解耦。
- GitHub owner：全部 `mouriya-s-lab`。日常 git/gh 用 RiriAgent 账号。
- 凭据从 IaC/SOPS 取，不在对话里复述。

## 1. 定位

开发者社区（维护 OpenAlice: https://github.com/TraderAlice/OpenAlice）的 QQ 机器人，AstrBot 插件形态。
本质是「QQ 群 ↔ paseo agent 开发环境」的桥接层。paseo 后面的 AI（大概率 omp/pi）、system prompt、AI 插件全部留给用户，我们只做抽象。

参考：
- 架构参考：gsuid_core / WutheringWavesUID（指令分发 + HTML→图片渲染）
- 交互体感参考：`/Users/mouriya/Downloads/分享一个实用的原神Discord机器人：纳西妲 - 哔哩哔哩.pdf`
- 体感类似 claudetag

## 2. bot ↔ paseo 交互面（仅三件事）

| 方向 | 动作 |
|---|---|
| bot → paseo | 新建 session（按指令绑定 prompt 模板） |
| bot → paseo | 往指定 session 注入消息（携带 message_id） |
| paseo → bot | 本机反向 CLI，AI 自主决定回复形式：文字 / 文字模板 / 图片模板（AI 只填字段）/ 文件链接 / 表情包选择器 |

- 维护 QQ（platform:group:user:message）↔ paseo session 映射。
- paseo = https://github.com/getpaseo/paseo，本地 `~/Ext/code/paseo`，已打补丁 https://github.com/mouriya-s-lab/paseo/pull/2（便于部署）。paseo 分前端 app + 后端 daemon。
- session 关闭：bot 端可 resume 唤醒；harness 端或 paseo 侧车检测 **12h 无互动 → 关闭**。
- 偏长内容一律走图片模板渲染，不直接输出长文本。

## 3. 指令集

| 指令 | 行为 |
|---|---|
| `/需求 <内容>` | 记录需求 + 按模板启动 paseo session |
| `/收藏` + 引用消息（必须显式指令，不是 @bot 默认行为） | 把别人说过的话加入收藏夹（可含图片） |
| 分页查看 收藏夹 / 需求列表 | 渲染为图片输出 |
| `/帮我调查 <内容>` | AI 自选：直接回 QQ，或产出调查报告文件（md）并回链接 |
| 生成入口 URL（指向某 session） | 指令中 @ 了 N 个群友 → 生成 N 份独立 token/URL，逐个 @ 发送 |
| 发送 GitHub 链接 | 识别 issue / PR → 触发解读；支持写死默认仓库 OpenAlice |

## 4. Prompt 模板

- 不是纯文本：frontmatter + 正文（类 rule.md / skill.md）。
- frontmatter 配置：harness（omp/pi）、model、provider、effort 等启动参数；以及**回复工具的参数形态**（纯文字 / 文字模板 / 图片模板 / 表情包选择器），因为输入与回复同场景配套。
- 正文为 prompt 本体，带变量占位（需求内容、发起人、群、引用消息等）。
- 模板目录即注册表：加文件 = 加指令能力。

## 5. Harness 端扩展（omp/pi 通用）

- 注入消息时携带 `message_id`，插件在生命周期内剥离，不透传给 AI。
- 回复必须走工具调用 → 消费该 id。
- AI 结束一轮时若 id 未被消费 → 插件自动追加提醒「你还没回复，原消息是：…」。
- 假设 harness 为 omp。omp 源码 `~/Ext/code/oh-my-pi`；插件 API 参考 `~/Ext/code/omp-config`（仅参考 API 形态，功能无关，omp 不按它配置）。
- 核心逻辑 harness 无关，omp/pi 各自薄适配。

## 6. 网关（反向代理层）

- 无账号密码，一次性 token：URL 携带随机 token，预注册于网关；**访问一次 或 6 小时超时** → 注销；已进入用户不踢出。要求简单实现。
- 安全反代本机特定路径白名单下的静态文件（如 `.md`）。
- `.md` 在线渲染：mermaid、尽量完整的 md 扩展、代码高亮。
- 必须 TLS；域名 `237575.xyz` 或 `237676.xyz`（看 IaC 哪个有权限），**必须子域名**，不占主路径。DNS 用 homelab-tf / pve-vctcn IaC 里的 Cloudflare token。
- 入口 URL 页面：显示 bot 状态、已有命令、prompt 模板列表；功能性即可。
- 分享出去的 paseo 页面：**只暴露右侧主区**（标签页、对话、输入框及全部功能），隐藏左侧侧栏（工作区/历史/搜索/计划/project 管理）。URL 可直达指定 session。需改 paseo 前端 → 从 `mouriya-s-lab/paseo` 再 fork 一次，定制放 `fork-features/`。

## 7. 存储与部署

- 存储：DuckDB（需求、收藏、session 映射、token 表）。
- 环境：Linux 开发服务器 `nekoringo2`。
- paseo 容器化，基底镜像 **Arch Linux**。
- AstrBot 必须容器化，同机。
- QQ 协议端：调查跑在哪、能否容器化（NapCat / Lagrange / LLOneBot 等），重点是**掉线/封号风险**；可接受 Linux 容器 + SR-IOV iGPU 加速，前提是不频繁被踢。
- 插件仓库：本目录 `~/Ext/code/alicedev` → 新建 `mouriya-s-lab/alicedev`。
- DNS：alicedev.237575.xyz A 160.191.41.242（DNS-only，2026-09-19 经 CF API 手工创建，record id efbc4a92a4a27ea2a5e9bfad5a7f55da；nekoringo2 不归 IaC，若日后纳入 pve-vctcn/apps/dns 需迁移该记录）

## 8. 测试

- **用户决定（2026-09-19）：端到端测试用 AstrBot 自带 WebChat（dashboard 内置聊天，平台名 `webchat`），通过 `agent-browser` 驱动；不做 Telegram 测试。**
- Telegram 适配器仍按用户给的 token 接入（token 写入部署 env，不写入本文件；bot id 8838419390 / Riri2），但不作为验证路径。WebChat 覆盖不到的能力（如 At 多人 fan-out、Reply 引用）在报告里标「未在真实平台验证」。
- 本机 `tg` = kabi-tg-cli，用户 2026-09-19 已重登（user id 865341181）；仅作备用，不主动用。
- 平台接入由 AstrBot 负责。

## 9. 默认决策（用户已授权）

- session 续注入：引用 bot 回复 / 显式 `/继续 <id>`；不自动续。
- 权限：群白名单 + 管理员列表在插件配置；生成 URL 仅管理员。
- 反向 CLI：本机 unix socket，无网络暴露。
- 收藏标识：`platform:group:user` 三元组，QQ/TG 通用。
- 调查报告落盘：固定目录（网关白名单根），AI 写文件后回 token URL。
- 网关：轻量自写反代；表情包目录配置；`/需求` 全员可用；UI 文案中文。

## 10. 进度

见 `todo` 与 `ctx list`。阶段：Framing → Design → Build → Verification。
