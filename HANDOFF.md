# alicedev — 交接文档

> 防 compact 遗忘的交接。新 session 顺序：**先读 `AGENTS.md`，再读 `ARCHITECTURE.md`，再读本文件**，然后 `ctx list` 定位进度。本文件只记状态、待办、待清理、待转移；契约在 ARCHITECTURE.md。

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

**架构五条不变量（ARCHITECTURE.md §0，违背即错）**：monorepo（全部代码在本仓库；paseo 按上游原样使用，不 fork、不改源码）；paseo daemon 只是一个不可修改的已部署 daemon，**bot → daemon 唯一接触面是 paseo CLI（经 `paseoctl` shim 跨容器调用），禁 MCP、禁 WS；agent → bot 唯一通道是回复（`/v1/reply`）；agent 里的 AI 是执行者、不碰 paseo CLI、不推进流程，会话状态由 AI 经回复的 `transition` 报告**；bot 侧三个独立职责 —— 传输（出站是消息队列）/ 调度（会话状态机；全部 AI 业务都是场景，旧会话业务是单对话态场景，`/升级bot` 是多状态场景）/ 可见性；IaC 只 provision 宿主文件（`deploy/` 与 `.env`）、与运行时解耦；指令、路由、场景、回话全部 DSL 化（`templates/`），代码不写死指令。

## 2. 线上现状（nekoringo2，compose 项目 `deploy`，`/srv/alicedev`）

- 入口 https://alicedev.237575.xyz；`caddy` `gateway` `astrbot` `snowluma` `paseo` `t2i` 常驻，`astrbot-init` `snowluma-init` `reports-init` 一次性。`e2e`、`tg-cli` **尚未上生产**（§6.5）。
- 普通会话链路（`/需求` `/继续` `/需求列表` `/收藏` `/收藏夹` `/帮我调查` `/链接` `/解读` `/归档` `/alicedev`）已在 Telegram 与 WebChat 真机验证，但其 bot→daemon 传输是 MCP（`bot/alicedev/paseo/mcp.py`），**违背不变量 2，待返工为 paseo CLI shim**（§6.2）。QQ：NapCat 已删除（容器 + 卷），snowluma 已上生产、容器 RUNNING、反向 WS 已配到 astrbot；**QQ 账号 3775719932 尚未在 snowluma 里扫码登录**，登录后需 `docker restart alicedev-astrbot` 加载 platform id `qq`。
- 已落地的近期变更：会话/current 指针重构与命名会话；一次性链接 GET/HEAD 只 peek、POST 才消费（防 Telegram 预览烧 token）；DuckDB 迁移事故恢复（pin `duckdb==1.5.5`，迁移后 CHECKPOINT）；网关 `/_alicedev/health` 公开；**管理面板绑 netbird 接口**（`PANEL_BIND_IP=100.85.238.88`：AstrBot dashboard `:6185`、snowluma noVNC `:6081` / WebUI `:5099`，公网不可达）。alicedev `main` @ `5c755e0`；`feat/qq-snowluma` @ `06b06b4`（已 push，未合 main，= 生产 deploy 目录的声明来源）。
- 主机：nekoringo2 `160.191.41.242`（netbird `100.85.238.88`），nekoringo1 `218.33.108.254`（Komodo Core，netbird `100.85.171.99`）。SSH `root@…` key `~/.ssh/dev-dai`。
- 基础设施仓库 `~/Ext/code/nekoringo-iac`（OpenTofu：`host/` netbird、`apps/komodo/`、`apps/alicedev/`）：owner `mouriya-s-lab`，push 到 `github.com/mouriya-s-lab/nekoringo-iac`（`main`）。`apps/alicedev` 已重设计为 **provision-only** 且 `tofu apply` 落地（见 §7）：IaC 只 provision 宿主文件、与运行时健康/QQ 彻底解耦。
- 凭据位置：`deploy/.env`（0600）持 `GATEWAY_SECRET` `ALICEDEV_INTERNAL_TOKEN` `PASEO_PASSWORD` `OPENCODE_API_KEY` `ONEBOT_ACCESS_TOKEN` `VNC_PASSWD` 等（SOPS `alicedev_deploy_env_b64` 与之一致）；omp provider key 在 `paseo_home` 卷 `/home/paseo/.omp/`；dashboard 密码 `nekoringo2:/root/alicedev-dashboard-password`。paseo `/workspace` 卷只有 `/workspace/openalice`；`/workspace/alicedev` 为空目录。

## 3. `/升级bot` 闭环：本轮事故与真实状态

### 3.1 发生了什么
上一轮实现把架构理解错了：把 paseo 当成可 fork / 可改 / 需在 harness 层驱动的东西。后果：调度程序（conductor）写进了 paseo fork；用 `paseo run --output-schema` 直接驱动 pi 并逼模型吐 JSON；在 paseo home 里临时配 provider；未经授权用付费模型 `opencode-go/glm-5.2` 跑测试；本机 build paseo dist、起临时 daemon；ARCHITECTURE.md 全程未对齐、未更新。**"真 e2e 跑通"是建在错架构上的，不作验收。** 生产自始至终零改动。

### 3.2 盘上现有代码（按去留分类）

**alicedev 分支 `feat/upgrade-bot-loop` @ `f1ceee8`（已 push，未合 main）— 位置正确，需按 ARCHITECTURE §6/§7/§13 改造。其中 `POST /v1/upgrade/callback`、`GET /v1/upgrade/state`、`botctl`、`upgrade_runs` 已不在契约内：状态改走 `/v1/reply` 的 `transition`，表改为通用的会话表 `sessions`**
- bot：`bot/alicedev/commands/upgrade.py`（`/升级bot` start/status/approve/reject，管理员限定，commit 绑定）、`bot/alicedev/store/upgrade_runs_repo.py` + `schema.sql` 的 `upgrade_runs`、`bot/alicedev/api/server.py` 的 `POST /v1/upgrade/callback` + `GET /v1/upgrade/state`、`bot/alicedev/api/reply.py` 内联 Image 回复路径、`bot/alicedev/render/cards.py` + `render/cli.py` + `templates/cards/upgrade_{explain,evidence}.html` 证据图渲染。`main.py` 已接线。
- 工具：`tools/{botctl,tgctl,deployctl,deployrun,mainsync,e2e_driver.py,bootstrap-fixed-main.sh}`（+ README）。`deployctl` 已修一个真 bug（health 探针输出漏进 JSON stdout）。
- 基建：`deploy/e2e/`（隔离专用镜像，运行时现挂 bot 源，随机 loopback 端口，无生产凭据；契约改为常驻、由 agent 切 `bot/` 与 `templates/` 到指定 SHA 后热重载，ARCHITECTURE §11，需改造）、`deploy/tg-cli/`（持久 Telegram 容器 + Telethon `tg photo`）。
- 隔离验证过：状态机、四类回调 → Plain/Image/Image、`botctl` 全错误码、`deployrun` 三态、`mainsync` fail-closed、`e2e_driver` 真 agent-browser 双侧对称抓图（distance 0）、e2e 栈真渲卡、tg 会话跨重启。
- **缺的**：`upgrade.py` 里 conductor 是 stub（Protocol），因调度程序当时写去了 fork。

**paseo-alicedev 分支 `feat/upgrade-bot-conductor` @ `dc62397ee`（已 push）+ 工作树未提交改动 — 位置错误，机制错误**
- `tools/conductor.py` `conductorctl` `conductor_test.py` `conductor_schemas/` `conductor.config.json`（未提交）、`tools/worktreectl`、`.agents/skills/upgrade-bot-{conductor,impl,test,e2e}/`、`fork-features/upgrade-bot.md`。
- 可搬的**内容**：状态顺序与四个事件（→ upgrade-bot 场景的状态与可见状态）、收敛判定（子集单调 + 距离不增 + 至少一严格降；green = ∅/∅/0，→ `working` 状态 prompt）、fixed-main 绊线（→ 调度派发）、`deployrun` 接线（→ `deploying` 状态 prompt）、skill 里各角色职责文本（→ 状态 prompt / `skills/`）。
- 必须丢的**机制**：`RealSubagentRunner`（`paseo run --output-schema`、解析 LLM JSON）、`worktreectl` 包 paseo CLI、fork 内 provider/model 配置、`fork-features/upgrade-bot.md`。

**临时物（本机）**
- `~/Ext/tmp/upgrade-e2e-lab/`：paseo dist + 临时 PASEO_HOME + 临时 daemon（127.0.0.1:6799，进程 env 含 OPENCODE_API_KEY）。
- 本机隔离容器：compose 项目 `alicedev-e2e`、`alicedev-tg-cli`；e2e_driver 的 worktree 与截图产物在 `docs/evidence/`（`deploy/` 下的 e2e 证据目录）。

### 3.3 已定的设计（ARCHITECTURE.md §3、§6、§7、§13，此处只列要点）
指令、消息路由、场景和用户可见的固定文字全部是 `templates/` 下的 YAML DSL，代码只提供封闭的动作、参数类型与卡片渲染；DSL 可直接出帮助卡、指令卡、会话卡与场景流程图。指令分程序指令与 AI 指令；AI 指令新开会话，或发到已有会话（`%n` 指定，省略则取引用消息所属会话、本群当前会话）；会话号是本群自增整数，会话内部为每个 agent 状态起的 paseo 运行单元叫 agent；会话有完整的列表/详情/切换/重命名/归档指令。全部 AI 业务都是场景：`/需求`、`/帮我调查`、`/解读` 是单对话态场景（一个带 `reply` 的 agent 状态，无 `repo`、不独占，只由 `/归档` 结束，agent 创建失败时进入 `failed`），`/升级bot` 是多状态场景。出站是消息队列：`/v1/reply` 只入队（202），outbox worker 出队发平台。调度是 bot 侧的会话状态机（`bot/alicedev/scheduler/`）：场景 = 工作目录或挂钩仓库与 worktree 生命周期 + 状态机 + 状态 prompt（`templates/scenarios/<name>/`）；声明独占的场景一次只处理一个会话；状态由当前 agent 的 AI 经回复 `transition` 报告，调度校验后推进并经 paseo CLI 起下一状态的 agent；只有可见状态进聊天。bot → daemon 只走 paseo CLI（`paseoctl`，禁 MCP/WS），AI 不碰 paseo CLI。`/升级bot` 本质是 issue → PR 工作流：fixed-main 只对齐不跑 AI，ff 失败 → `main_sync_failed` + 链接；`working` 一个状态内建 issue、实现、测试、e2e、开 PR，e2e / tg-cli 的用法、before/after 证据、收敛判定都写在该状态 prompt 里；批准即 merge（绑定 PR head commit）；`deploying` 由 AI merge PR → fixed-main 拉取 main → 经 `deployrun` 宿主检出切到 main SHA + 插件热重载 + 核实，失败切回重载；模型 = daemon provider 配置里的 task:low 同款 `muse-spark-1.3-contributor`（opencode id `-free`）。

## 4. 应清理

| 项 | 动作 |
|---|---|
| 本机临时 daemon（6799）与 `~/Ext/tmp/upgrade-e2e-lab/` | 停进程（env 含 API key）、删目录 |
| 本机隔离容器 `alicedev-e2e`、`alicedev-tg-cli` 及其卷 | `docker compose -p <项目> down -v`；生产版本另建（§6） |
| paseo-alicedev 工作树未提交改动（`conductor.py` 解析器、`conductor.config.json`） | 丢弃（`git checkout -- tools/ && git clean -fd tools/`），先把 §5 要搬的逻辑摘走 |
| paseo-alicedev 分支 `feat/upgrade-bot-conductor` 及整个 fork | 搬完逻辑后删除分支；paseo 镜像去 fork 化（§6.9）后不再使用 `paseo-alicedev` |
| alicedev 分支上误入的东西 | 复核 `tools/README`、`docs/evidence/` 里指向 fork 路径 / `--output-schema` / `worktreectl` / MCP 的说明并改正 |
| `omp-config` 未提交改动（`agent/config.yml`：session_search 分组 + task:low 加 `openai-codex/gpt-5.6-luna:max`）| 与本项目无关，是用户自己的改动，**不动** |
| `komodo/syncs/alicedev.toml`（Komodo Stack 路线已废） | 删除，alicedev 归 nekoringo-iac |

## 5. 应转移（paseo fork → 本仓库）

| 来源（paseo-alicedev） | 去向（alicedev） | 改造 |
|---|---|---|
| `tools/conductor.py` 的状态顺序 / fixed-main 绊线 / deployrun 接线 | 通用调度 `bot/alicedev/scheduler/` + 场景定义 `templates/scenarios/upgrade-bot/` —— 逻辑曾迁到 `bot/alicedev/upgrade/`（未提交，见 §6.2），需再改造 | worktree 与 agent 生命周期走 **paseo CLI（`paseoctl` shim）**；状态推进改为 agent 回复的 `transition`，不再 `wait`/`inspect` 判完成、不读 worktree 判状态；删除 `--output-schema` 与 JSON 解析；收敛判定移入 `working` 状态 prompt |
| `conductor_test.py` 的状态机测试 | `bot/tests/`（已迁为 `test_conductor.py`，未提交） | 改测会话调度：transition 校验、独占派发、human 指令、恢复 |
| `conductor_schemas/{impl,test,e2e}.schema.json` | 场景状态 prompt 里的"完成标准"与各状态要求的 `transition.data` | 不再作为模型输出 schema |
| `.agents/skills/upgrade-bot-{impl,test,e2e}/` | 场景状态 prompt（`templates/scenarios/upgrade-bot/*.md`）与 `skills/`（挂载给 paseo agent；conductor skill 不需要，调度是程序） | 去掉一切 `paseo run` / MCP / fork 路径引用 |
| `tools/worktreectl` | 删除 | worktree 建/归档改调 `paseoctl workspace create/archive` |
| `conductor.config.json` 的 provider | daemon 的 provider 配置（`paseo_home`，部署配置）+ 场景 DSL 的 `provider` 字段引用其 id | 不在代码里写死 |

## 6. 接下来要做（顺序即依赖；每步真实验证后再下一步）

1. **QQ 协议端换 SnowLuma —— 已上生产（2026-09-22），残留仅人工扫码。** napcat 已 `docker rm -f` + 删卷；snowluma + snowluma-init 在 compose（digest `sha256:28efabd8…`），提交为 alicedev `feat/qq-snowluma` @ `06b06b4`（push 到 `mouriya-s-lab/alicedev`）并由 IaC 声明落地（§7）。要点存档：
   - 镜像 `motricseven7/snowluma`；**必须** `SYS_PTRACE`+`seccomp=unconfined`+`shm-size=1g`+`ulimit nofile`。端口 `6081` noVNC / `5099` WebUI 绑 `${PANEL_BIND_IP}`。OneBot 反向 WS 走 `config/onebot.json` 的 `wsClients.url=ws://astrbot:6199`（由 `snowluma_onebot.json` seed，token `ONEBOT_ACCESS_TOKEN`；astrbot aiocqhttp server 端不变）。卷 `qq-gateway-data`/`qq-client-config`/`qq-client-data` = 持久设备身份，重启不掉线（替 NapCat 的理由）。
   - **残留（运行时层，非 IaC，gate 不了 e2e/IaC）**：① 人工扫码登录 QQ —— `http://nekoringo2.mouriya.lan:6081/`，远程桌面密码 `docker logs alicedev-snowluma | grep 'remote desktop password'`，桌面内扫码；② 扫码后 `docker restart alicedev-astrbot` 加载新 platform id `qq`（现仍跑旧 id，token 值相同故 snowluma↔astrbot 仍认证）。
2. **bot → daemon 控制面返工（当前工作，未做）。** 用户 2026-09-22 定稿（已落 ARCHITECTURE §0/§4、AGENTS §2.2）：**bot 与 daemon 唯一接触面 = paseo CLI，经 `tools/paseoctl` shim 跨容器调用（`docker exec alicedev-paseo paseo … --json`）；禁止 MCP、禁止 WS；调度是 bot 做，agent 里的 AI 是执行者，不碰 paseo CLI。** CLI 子命令面（源码初查 `paseo-alicedev/packages/cli/src/commands/{workspace,agent}/`）：`workspace create/ls/archive`、`run`、`wait`、`inspect`、`ls`、`send`、`stop`、`archive`，均带 `--json`/`--host`；**具体参数与 JSON 形状以实现时在 paseo 容器内 `paseo <cmd> --help` / `--json` 实测为准**，不以源码阅读代替。
   - **作废（错架构，不作依据）**：`bot/alicedev/paseo/mcp.py`（MCP，生产普通链路在用 —— 从一开始就错）；2026-09-23 未提交的 `paseo/daemon_ws.py` + `control.py` 的 `DaemonControl/WaitResult/WorktreeRef` + `bot/tests/test_daemon_ws.py`（WS）。
   - **待改造**：未提交的 `bot/alicedev/upgrade/`（`state.py`、`convergence.py`、`conductor.py`：升级专用状态机 / 收敛判定 / 四事件 / `ToolsMainSync` / `ToolsDeployRunner` / `UpgradeEventSink`）+ `test_conductor.py`/`test_convergence.py`。按 ARCHITECTURE §7 改为通用调度 `bot/alicedev/scheduler/`：状态由回复 `transition` 推进；升级专用的状态与事件移入场景定义；收敛判定移入 `working` 状态 prompt。
   - 要做：① `tools/paseoctl` shim；② `bot/alicedev/paseo/` 的 `PaseoControl` 改为 CLI 实现（普通链路 + 调度同一套），删 `mcp.py`/`daemon_ws.py`；③ `/v1/reply` 改为入队（202）+ outbox worker 出队，`chat_reply` 支持 `transition`（ARCHITECTURE §5/§6）；④ 通用调度 + 场景定义 loader，旧会话业务迁为单对话态场景（`templates/prompts/*` → `templates/scenarios/`，建会话/当前指针/注入并入调度，现 `sessions` 表拆为会话与 `agents`，会话号改为本群自增 `%n`，`requirements` 表并入会话）；⑤ 隔离验证必须同架构（bot → paseoctl → CLI → daemon；agent 回复 → `/v1/reply`）。全部未提交、未验证。
3. **建 alicedev fixed-main**（生产，未做）：paseo `/workspace/alicedev` 现为空目录，clone alicedev 作 fixed-main（只对齐）；配 daemon provider（task:low 同款）；验证 `mainsync align` 与派生 worktree。
4. **upgrade-bot 场景（未做）**：写 `templates/scenarios/upgrade-bot/`（`scenario.yaml` + `working` / `deploying` 两个状态 prompt：前者含 issue → PR 流程、e2e / tg-cli 用法、before/after 证据、收敛判定、说明图与证据图经 `render/cli.py` 渲染到 `reports_dir`，后者含 merge、拉取 main、`deployrun`）；`skills/upgrade-bot-*` 从 fork 迁回并挂载给 paseo agent；删除 fork 里的 conductor 分支/代码（§4/§5，**至今未清**）。
5. **e2e / tg-cli 上生产**（未做）：nekoringo2 起独立常驻 `e2e`、`tg-cli`（不属 `deploy` 项目）；tg 一次性人工登录；e2e 常驻，被测的 `bot/` 与 `templates/` 由 agent 切到指定 SHA 后热重载插件。
6. **宿主 bot 转 git 检出**（未做）：`/srv/alicedev/bot` 现为 rsync 落地；改为 `deployctl` 拥有的 alicedev git 检出，astrbot 挂载其中的 `bot/` 与 `templates/`（DuckDB 卷不动）；实测 `deployctl` 触发插件热重载的程序化入口；先做一次 no-op 部署证明 切 SHA + 重载 + 核实 + 切回 可用。
7. **真 e2e 验收**（闸门）：在生产 daemon + 生产 e2e/tg-cli 上跑一条真需求：upgrade-bot 场景每个可见状态各真触发一次（`awaiting_approval` 的说明图 + 证据图真投递到 QQ/TG、approve 后 `active`、`rolled_back`、`deploy_failed`、`needs_human`、`rejected`、`archived`、`failed`、`main_sync_failed` 绊线）。证据入 `docs/evidence/`。
8. 合并 `feat/upgrade-bot-loop` 到 main，更新 runbook；nekoringo-iac `apps/alicedev` 管理清单纳入新增服务。
9. **paseo 去 fork 化**（未做）：paseo 是不可修改的已部署基础设施，不 fork、不改源码。compose 的 `paseo-base` 现在从 `paseo-alicedev` 的 `docker/arch/Dockerfile` 构建（含 embed 模式与 Arch 镜像两项 fork 改动）；改为上游 paseo 原样作基础，`deploy/paseo/Dockerfile` 只叠加 harness 产物、provider 配置、entrypoint 与 `omp`/`git`/`gh`；分享视图（只留右侧 agent 操作区）改由网关注入 `/_alicedev/static/paseo-view.css` 实现（用户 2026-09-23 定，ARCHITECTURE §8），`/链接` 与网关 token 校验的目标去掉 `embed=1`；fork 提交 `6a59f442` 修过的重复连接问题（自托管 manifest 重复探测导致回复不刷新，见 `docs/evidence/deploy-nekoringo2/embed-reply-sync.md`）去 fork 后会回来，需在网关侧实测后另解；runbook 里 `paseo-alicedev` 检出与构建步骤随之改写。基础镜像的上游来源待落实。验收：从 QQ/TG 真点分享链接，左侧栏不可见、能发消息并收到回复、刷新与前进后退后仍如此。
10. **指令 DSL 化**（未做，用户 2026-09-23 定）：现有指令全部写死在 `bot/alicedev/commands/*.py`；改为 `templates/commands/*.yaml`、`templates/routes.yaml`、`templates/messages.yaml` 加场景 DSL（ARCHITECTURE §3），代码只保留 `alicedev/dsl/` 与 `alicedev/actions/`；帮助卡、指令卡、会话卡从 DSL 生成；`python -m alicedev.dsl check|card` 离线校验与出图。交付随之改变：`templates/` 由 `deployctl` 检出与 `bot/` 一起交付，nekoringo-iac `apps/alicedev` 的托管集去掉 `templates/`（§7 记录的 apply 仍含 `templates/`）。验收：在 WebChat/QQ 逐条真发每个内置指令，已有指令的回话与旧行为一致，新增的会话指令（列表/详情/切换/重命名/归档、`%n` 指定、引用优先）行为符合 ARCHITECTURE §7/§9；`/alicedev` 与 `/alicedev <指令>` 出图；故意写错一个指令文件，它被拒绝且 `/v1/status` 报出文件与行号，其余指令照常。

## 7. 已决并已执行（原两问）

- **git owner = `mouriya-s-lab`**（用户定）。`nekoringo-iac` push 到私有库 `github.com/mouriya-s-lab/nekoringo-iac`（`main`：`e075ab6` 初始 + `c0e52b7` provision-only 重构）。日常 git/gh 用 RiriAgent。
- **`apps/alicedev` IaC —— 重设计为 provision-only 并已 `tofu apply`。** 原模块把 apply 耦合到运行时（容器健康 + QQ-online 的 fail-closed adopt）是**架构错误**（用户拍板 + discuss:steady/divergent + mentor 三方一致）：IaC 只声明 + provision 宿主文件，`apply` 成功 = 声明落地，**绝不**跑 compose、判健康、验 QQ、记 ownership；不健康不靠重跑 apply 修。边界见 `nekoringo-iac/apps/alicedev/README.md`。落地事实：
  - 删掉 check/adopt/release 模式、QQ/health gate、ownership marker；`terraform_data` 按 artifact digest re-key；源 = committed `06b06b4`（`git archive deploy+templates`，**排除 `bot/`** —— 否则一次 apply 会回滚已批准的 /升级bot 部署）；`deploy/.env` 从 SOPS 流式注入（明文不进 state/argv/log）。
  - SOPS `alicedev_deploy_env_b64` 已从 live 重新加密（含 `PANEL_BIND_IP`、`ONEBOT_ACCESS_TOKEN`，无 `NAPCAT_*`）。
  - `tofu apply` 成功（digest `60f0766`，`terraform.tfstate` 已建，宿主文件 = 声明一致）。**QQ 登录 / 容器健康是运行时层，gate 不了 apply**；bring-up 归 `make up` / `deployctl`。
  - divergent 的替代路线（A：交给 Komodo `komodo/syncs/alicedev.toml` 拥有 Stack，需先解决 gateway/paseo 镜像来源）未采纳 —— 尊重"alicedev 归 nekoringo-iac"；若日后要，见 `history://DivergentBoundary`。

## 8. 参考

- 契约：`ARCHITECTURE.md`。运维：`docs/runbook.md`。证据：`docs/evidence/`。paseo daemon API 事实：`docs/research/paseo-api.md`。
- 之前的设计交接（§2 交互面、§3 指令集、§4 模板、§6 网关、§9 默认决策）已全部并入 ARCHITECTURE.md，此处不再重复。
