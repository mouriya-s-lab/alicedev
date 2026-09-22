# alicedev — 交接文档

> 防 compact 遗忘的权威交接。新 session 顺序：**先读 `AGENTS.md`，再读 `ARCHITECTURE.md`（v3），再读本文件**，然后 `ctx list` 定位进度。契约以 ARCHITECTURE.md 为准；本文件只记状态、待办、待清理、待转移。

## 0. 工作方式约束（用户明确要求）

- 用户说完需求后自主完成，遵照 system prompt 编排流程；不为琐事反复确认，但**动生产前必须过闸门**。
- 必须在本地留交接（本文件 + `docs/`），持续更新 `todo`，用 `ctx` 定位进度。
- 子代理：`task:mid` / `task:low` 用量无限，均 Opus 级但**可能撒谎**；根因通常是任务太大。对策：切小、交叉验证、只认 artifact 与运行证据。`task:high` 贵，仅用于必须自行定方案的切片。
- **没有真实 e2e = 没做**。stub / fake runner / 单测不算验收。
- 架构必须可扩展，禁面条代码。重点解耦：指令注册/分发、bot↔paseo 通讯层、渲染层、平台适配层。
- GitHub owner 全部 `mouriya-s-lab`；日常 git/gh 用 RiriAgent。私有库到服务器走 rsync（服务端无 deploy key）。
- 凭据从 IaC/SOPS 取，不在对话里复述。用户明确：面板密码类泄漏在非公网服务上不重要，不做说教、不强制轮换。
- 用户禁止：hand-code 逐文件自己写（用 `task` 编排）、拿 git 子命令当验证、跑未授权的付费模型。

## 1. 定位

开发者社区（维护 OpenAlice: https://github.com/TraderAlice/OpenAlice）的 QQ/Telegram 机器人，AstrBot 插件形态。本质是「群聊 ↔ paseo agent 开发环境」的桥接层。paseo 后面的 harness（omp / pi）、system prompt、AI 插件留给用户，我们只做抽象。

**架构三条不变量（ARCHITECTURE.md §0，违背即错）**：monorepo（全部代码在本仓库，paseo fork 只做 embed + Arch 镜像）；paseo daemon 是不可修改的已部署基础设施，被动响应 API、由 RPC 驱动 harness 并自行整理产出；bot 侧三个独立职责 —— 传输 / 调度 / 可见性。

## 2. 线上现状（nekoringo2，compose 项目 `deploy`，`/srv/alicedev`）

- 入口 https://alicedev.237575.xyz；`caddy` `gateway` `astrbot` `napcat` `paseo` `t2i` 常驻 healthy，`astrbot-init` `reports-init` 一次性。
- 普通会话链路（`/需求` `/继续` `/需求列表` `/收藏` `/收藏夹` `/帮我调查` `/链接` `/解读` `/归档` `/alicedev`）已在 Telegram 与 WebChat 真机验证。QQ 走 NapCat（账号 3775719932）：**设备身份不持久，每次容器重启即下线**，当前离线（`check` 见 §7）；因此**不再以此 NapCat QQ 为基准**，QQ 方案改用 SnowLuma（§6.1）。
- 已落地的近期变更：会话/current 指针重构与命名会话；一次性链接 GET/HEAD 只 peek、POST 才消费（防 Telegram 预览烧 token）；DuckDB 迁移事故恢复（pin `duckdb==1.5.5`，迁移后 CHECKPOINT）；网关 `/_alicedev/health` 公开；NapCat hostname 固定 `DESKTOP-Q7M3K8P`；**管理面板改绑 netbird 接口**（`PANEL_BIND_IP=100.85.238.88`，`nekoringo2.mouriya.lan:6099/6185`，公网不可达，SSH 隧道已废）。alicedev `main` @ `5c755e0`。
- 主机：nekoringo2 `160.191.41.242`（netbird `100.85.238.88`），nekoringo1 `218.33.108.254`（Komodo Core，netbird `100.85.171.99`）。SSH `root@…` key `~/.ssh/dev-dai`。
- 基础设施仓库 `~/Ext/code/nekoringo-iac`（OpenTofu：`host/` netbird、`apps/komodo/`、`apps/alicedev/`）：owner `mouriya-s-lab`，push 到 `github.com/mouriya-s-lab/nekoringo-iac`（`main`）。`apps/alicedev` 已重设计为 **provision-only** 且 `tofu apply` 落地（见 §7）：IaC 只 provision 宿主文件、与运行时健康/QQ 彻底解耦。
- 凭据位置：`deploy/.env`（0600）持 `GATEWAY_SECRET` `ALICEDEV_INTERNAL_TOKEN` `PASEO_PASSWORD` `OPENCODE_API_KEY` `NAPCAT_ONEBOT_TOKEN` 等；omp provider key 在 `paseo_home` 卷 `/home/paseo/.omp/`；dashboard 密码 `nekoringo2:/root/alicedev-dashboard-password`。paseo `/workspace` 卷只有 `/workspace/openalice`；`/workspace/alicedev` 为空目录。

## 3. `/升级bot` 闭环：本轮事故与真实状态

### 3.1 发生了什么
上一轮实现把架构理解错了：把 paseo 当成可 fork / 可改 / 需在 harness 层驱动的东西。后果：调度程序（conductor）写进了 paseo fork；用 `paseo run --output-schema` 直接驱动 pi 并逼模型吐 JSON；在 paseo home 里临时配 provider；未经授权用付费模型 `opencode-go/glm-5.2` 跑测试；本机 build paseo dist、起临时 daemon；ARCHITECTURE.md 全程未对齐、未更新。**"真 e2e 跑通"是建在错架构上的，不作验收。** 生产自始至终零改动。

### 3.2 盘上现有代码（按去留分类）

**alicedev 分支 `feat/upgrade-bot-loop` @ `f1ceee8`（已 push，未合 main）— 位置正确，可留，需按 §15 契约复核**
- bot：`bot/alicedev/commands/upgrade.py`（`/升级bot` start/status/approve/reject，管理员限定，commit 绑定）、`bot/alicedev/store/upgrade_runs_repo.py` + `schema.sql` 的 `upgrade_runs`、`bot/alicedev/api/server.py` 的 `POST /v1/upgrade/callback` + `GET /v1/upgrade/state`、`bot/alicedev/api/reply.py` 内联 Image 回复路径、`bot/alicedev/render/cards.py` + `render/cli.py` + `templates/cards/upgrade_{explain,evidence}.html` 证据图渲染。`main.py` 已接线。
- 工具：`tools/{botctl,tgctl,deployctl,deployrun,mainsync,e2e_driver.py,bootstrap-fixed-main.sh}`（+ README）。`deployctl` 已修一个真 bug（health 探针输出漏进 JSON stdout）。
- 基建：`deploy/e2e/`（隔离专用镜像，运行时现挂 bot 源，随机 loopback 端口，无生产凭据）、`deploy/tg-cli/`（持久 Telegram 容器 + Telethon `tg photo`）。
- 隔离验证过：状态机、四类回调 → Plain/Image/Image、`botctl` 全错误码、`deployrun` 三态、`mainsync` fail-closed、`e2e_driver` 真 agent-browser 双侧对称抓图（distance 0）、e2e 栈真渲卡、tg 会话跨重启。
- **缺的**：`upgrade.py` 里 conductor 是 stub（Protocol），因调度程序当时写去了 fork。

**paseo-alicedev 分支 `feat/upgrade-bot-conductor` @ `dc62397ee`（已 push）+ 工作树未提交改动 — 位置错误，机制错误**
- `tools/conductor.py` `conductorctl` `conductor_test.py` `conductor_schemas/` `conductor.config.json`（未提交）、`tools/worktreectl`、`.agents/skills/upgrade-bot-{conductor,impl,test,e2e}/`、`fork-features/upgrade-bot.md`。
- 可搬的**逻辑**：状态机顺序、四事件发射、收敛判定（子集单调 + 距离不增 + 至少一严格降；green = ∅/∅/0）、fixed-main 绊线、`deployrun` 接线、skill 里各角色职责文本。
- 必须丢的**机制**：`RealSubagentRunner`（`paseo run --output-schema`、解析 LLM JSON）、`worktreectl` 包 paseo CLI、fork 内 provider/model 配置、`fork-features/upgrade-bot.md`。

**临时物（本机）**
- `~/Ext/tmp/upgrade-e2e-lab/`：paseo dist + 临时 PASEO_HOME + 临时 daemon（127.0.0.1:6799，进程 env 含 OPENCODE_API_KEY）。
- 本机隔离容器：compose 项目 `alicedev-e2e`、`alicedev-tg-cli`；e2e_driver 的 worktree 与截图产物在 `docs/evidence/`（`deploy/` 下的 e2e 证据目录）。

### 3.3 已定的设计（全部在 ARCHITECTURE.md §15，此处只列要点）
调度程序在 bot 侧（`bot/alicedev/upgrade/`）；只走 daemon API；paseo 会话用 CLI + skill + pi-unified-exec，无 MCP；fixed-main 只对齐不跑 AI，ff 失败 → `main_sync_failed` + 链接；一 run 一 worktree 一组会话；5 轮不收敛才求助；对称 before/after 在 e2e 容器；说明图 + 证据图（Image）；批准绑 commit；部署 = git checkout + restart astrbot + 核实 + 自动回滚；模型 = daemon provider 配置里的 task:low 同款 `muse-spark-1.3-contributor`（opencode id `-free`）。

## 4. 应清理

| 项 | 动作 |
|---|---|
| 本机临时 daemon（6799）与 `~/Ext/tmp/upgrade-e2e-lab/` | 停进程（env 含 API key）、删目录 |
| 本机隔离容器 `alicedev-e2e`、`alicedev-tg-cli` 及其卷 | `docker compose -p <项目> down -v`；生产版本另建（§6） |
| paseo-alicedev 工作树未提交改动（`conductor.py` 解析器、`conductor.config.json`） | 丢弃（`git checkout -- tools/ && git clean -fd tools/`），先把 §5 要搬的逻辑摘走 |
| paseo-alicedev 分支 `feat/upgrade-bot-conductor` | 搬完逻辑后删除本地 + 远端分支；fork 回到只含 embed + Arch 镜像 |
| alicedev 分支上误入的东西 | 复核 `tools/README`、`docs/evidence/` 里指向 fork 路径 / `paseo run` / `worktreectl` 的说明并改正 |
| `omp-config` 未提交改动（`agent/config.yml`：session_search 分组 + task:low 加 `openai-codex/gpt-5.6-luna:max`）| 与本项目无关，是用户自己的改动，**不动** |
| `komodo/syncs/alicedev.toml`（Komodo Stack 路线已废） | 删除，alicedev 归 nekoringo-iac |

## 5. 应转移（paseo fork → 本仓库）

| 来源（paseo-alicedev） | 去向（alicedev） | 改造 |
|---|---|---|
| `tools/conductor.py` 的状态机 / 事件顺序 / 收敛判定 / fixed-main 绊线 / deployrun 接线 | `bot/alicedev/upgrade/`（astrbot 进程内后台任务，或同仓库独立进程；接 `commands/upgrade.py` 的 Protocol） | 子会话驱动改为 **daemon API**：起会话 → 等完成 → 读 daemon 状态 → 读 worktree git；删除 `--output-schema` 与 JSON 解析 |
| `conductor_test.py` 的状态机测试 | `bot/tests/` 或同目录 | 把 fake runner 改成 fake daemon client |
| `conductor_schemas/{impl,test,e2e}.schema.json` | 变成 skill 里对会话的"完成标准"描述 + 调度程序从 worktree/证据目录读取的约定 | 不再作为模型输出 schema |
| `.agents/skills/upgrade-bot-{impl,test,e2e}/` | `skills/upgrade-bot-*/`（挂载给 paseo 会话；conductor skill 不需要，调度是程序） | 去掉一切 `paseo run` / MCP / fork 路径引用 |
| `tools/worktreectl` | 删除 | worktree 建/归档改调 daemon API |
| `conductor.config.json` 的 provider | daemon 的 provider 配置（`paseo_home`，部署配置）+ bot 配置项 `upgrade_provider` | 不在代码里写死 |

## 6. 接下来要做（顺序即依赖；每步真实验证后再下一步）

1. **QQ 协议端换 SnowLuma —— 已上生产（2026-09-22），残留仅人工扫码。** napcat 已 `docker rm -f` + 删卷；snowluma + snowluma-init 在 compose（digest `sha256:28efabd8…`），提交为 alicedev `feat/qq-snowluma` @ `06b06b4`（push 到 `mouriya-s-lab/alicedev`）并由 IaC 声明落地（§7）。要点存档：
   - 镜像 `motricseven7/snowluma`；**必须** `SYS_PTRACE`+`seccomp=unconfined`+`shm-size=1g`+`ulimit nofile`。端口 `6081` noVNC / `5099` WebUI 绑 `${PANEL_BIND_IP}`。OneBot 反向 WS 走 `config/onebot.json` 的 `wsClients.url=ws://astrbot:6199`（由 `snowluma_onebot.json` seed，token `ONEBOT_ACCESS_TOKEN`；astrbot aiocqhttp server 端不变）。卷 `qq-gateway-data`/`qq-client-config`/`qq-client-data` = 持久设备身份，重启不掉线（替 NapCat 的理由）。
   - **残留（运行时层，非 IaC，gate 不了 e2e/IaC）**：① 人工扫码登录 QQ —— `http://nekoringo2.mouriya.lan:6081/`，远程桌面密码 `docker logs alicedev-snowluma | grep 'remote desktop password'`，桌面内扫码；② 扫码后 `docker restart alicedev-astrbot` 加载新 platform id `qq`（现仍跑旧 id，token 值相同故 snowluma↔astrbot 仍认证）；③ 服务器 `deploy/.env.bak.snowluma` 可删。
2. **daemon API 落地**：读 `paseo-alicedev/packages/client/src/daemon-client.ts` + `packages/protocol/src/messages.ts`，确认调度程序需要的调用：起会话（指定 workspace/worktree、provider、prompt/skill）、等完成、查状态与结果、建/归档 worktree、fixed-main 状态。现有 `bot/alicedev/paseo/` 已有 MCP-HTTP 传输（普通链路在用）；升级链路按 §15 加所需操作到 `PaseoControl`，不新造第二套客户端。
3. **建迭代 bot project**：paseo `/workspace/alicedev` clone alicedev，作 fixed-main（只对齐）；配 daemon provider（task:low 同款）；验证 `mainsync align` 与从它派生 worktree。
4. **调度程序迁回 bot 侧**（§5），接 `commands/upgrade.py`；skills 迁回并挂载给会话；删除 fork 里的对应物。
5. **e2e / tg-cli 上生产**：nekoringo2 起独立常驻 `e2e`、`tg-cli`（不属 `deploy` 项目）；tg 一次性人工登录；e2e 镜像可现挂任意 SHA。
6. **bot 目录转 git**：`/srv/alicedev/bot` 由 rsync 落地改为 git 检出（保留现有内容与 DuckDB 卷不动）；`deployctl` 指向它；先做一次 no-op 部署证明 checkout + restart + 核实 + 回滚可用。
7. **真 e2e 验收**（闸门）：在生产 daemon + 生产 e2e/tg-cli 上跑一条真需求：四类边界事件、说明图 + 证据图真投递到 QQ/TG、批准后真部署 active、`main_sync_failed` 绊线、`e2e_not_converging`（强制 5 轮不收敛）各真触发一次。证据入 `docs/evidence/`。
8. 合并 `feat/upgrade-bot-loop` 到 main，更新 runbook；nekoringo-iac `apps/alicedev` 管理清单纳入新增服务。

## 7. 已决并已执行（原两问）

- **git owner = `mouriya-s-lab`**（用户定）。`nekoringo-iac` push 到私有库 `github.com/mouriya-s-lab/nekoringo-iac`（`main`：`e075ab6` 初始 + `c0e52b7` provision-only 重构）。日常 git/gh 用 RiriAgent。
- **`apps/alicedev` IaC —— 重设计为 provision-only 并已 `tofu apply`。** 原模块把 apply 耦合到运行时（容器健康 + QQ-online 的 fail-closed adopt）是**架构错误**（用户拍板 + discuss:steady/divergent + mentor 三方一致）：IaC 只声明 + provision 宿主文件，`apply` 成功 = 声明落地，**绝不**跑 compose、判健康、验 QQ、记 ownership；不健康不靠重跑 apply 修。权威边界见 `nekoringo-iac/apps/alicedev/README.md`。落地事实：
  - 删掉 check/adopt/release 模式、QQ/health gate、ownership marker；`terraform_data` 按 artifact digest re-key；源 = committed `06b06b4`（`git archive deploy+templates`，**排除 `bot/`** —— 否则一次 apply 会回滚已批准的 /升级bot 部署）；`deploy/.env` 从 SOPS 流式注入（明文不进 state/argv/log）。
  - SOPS `alicedev_deploy_env_b64` 已从 live 重新加密（含 `PANEL_BIND_IP`、`ONEBOT_ACCESS_TOKEN`，无 `NAPCAT_*`）。
  - `tofu apply` 成功（digest `60f0766`，`terraform.tfstate` 已建，宿主文件 = 声明一致）。**QQ 登录 / 容器健康是运行时层，gate 不了 apply**；bring-up 归 `make up` / `deployctl`。
  - divergent 的替代路线（A：交给 Komodo `komodo/syncs/alicedev.toml` 拥有 Stack，需先解决 gateway/paseo 镜像来源）未采纳 —— 尊重"alicedev 归 nekoringo-iac"；若日后要，见 `history://DivergentBoundary`。

## 8. 参考

- 契约：`ARCHITECTURE.md`（v3）。运维：`docs/runbook.md`。证据：`docs/evidence/`。paseo daemon API 事实：`docs/research/paseo-api.md`。
- 之前的设计交接（§2 交互面、§3 指令集、§4 模板、§6 网关、§9 默认决策）已全部并入 ARCHITECTURE.md §3–§10，此处不再重复。
