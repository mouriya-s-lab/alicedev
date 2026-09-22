# AGENTS.md — alicedev 开发阶段规则

适用于在本仓库工作的每个 agent（主 session 与子代理）。顺序：先读本文件，再 `ARCHITECTURE.md`（契约），再 `HANDOFF.md`（状态与待办）。

## 1. 先对齐，再动手

- **契约变更先改 `ARCHITECTURE.md`，再写代码。** 用户口头定下的设计决定必须当场落到该文件；不落文件的决定视为不存在。
- 开工前对照 ARCHITECTURE.md §0 五条不变量与 §1 容器/工作目录表自查；任何一条对不上就停下改文档或问，不要顺着错前提往下建。
- 遇到和文档矛盾的现象（API 不支持、模型不支持、路径不存在），**先当作"我在错的层面干活"的信号**，回到架构核对；不要当局部 bug 打补丁。
- 只检查自己是不够的：核对**环境事实** —— 有几个容器、AI 工作目录在哪、有几个 project、daemon 的真实 API 长什么样。这些都在 `deploy/docker-compose.yml`、paseo `/workspace` 卷、paseo 容器内 `paseo <cmd> --help` / `--json` 里，不靠记忆。

## 2. 架构硬约束（违背即返工）

1. **monorepo**：所有 alicedev 代码只在本仓库。paseo 按上游原样使用，**不 fork、不改源码**；alicedev 只在其外面叠加运行配置（harness 产物、provider 配置，ARCHITECTURE §11）。
2. **paseo daemon 是已部署、不可修改的基础设施，只是一个 daemon**。被动响应；通过 RPC 驱动各 harness（omp / pi / claude / codex）并自行整理 agent 产出与状态。
   - **bot → daemon 的唯一接触面是 paseo CLI**（`paseo workspace create/archive`、`paseo run`、`paseo send`、`paseo inspect`、`paseo ls`、`paseo stop`、`paseo archive`，全部 `--json`），bot 经 `tools/paseoctl` shim 跨容器（`docker exec alicedev-paseo paseo …`）调用；bot 只读 CLI 的 JSON 结果。**禁止 MCP、禁止 WS、禁止自写 daemon 协议客户端。**
   - **agent → bot 的唯一通道是回复**（`alicedev-reply` → `POST /v1/reply`）。bot 不读 agent transcript。
   - **调度是 bot 的事，AI 是执行者**：agent 里的 AI **不碰 paseo CLI、不起 agent、不推进流程**；会话状态由 AI 通过回复的结构化 `transition` 字段报告，bot 调度校验后推进（ARCHITECTURE §6/§7）。**禁止**直接驱动 harness（`pi`、`omp` 命令行、`--output-schema`）、**禁止**解析模型自由文本当状态、**禁止**在 paseo home 里临时改 provider。
   - omp 与 pi 是两套不同的 harness（omp 建在 pi 底层之上），不可互换；本 session 自己跑在 omp 里，paseo 里的 agent 按场景 DSL 的 `provider`（daemon provider 配置里的 id）跑。
3. **bot 侧三件事分开**：传输（入站交给指令或路由；出站是消息队列，AI 与 bot 自己入队、平台适配器出队）、调度（会话状态机，场景 = 工作目录或挂钩仓库与 worktree 生命周期 + 状态机 + 状态 prompt；声明独占的场景一次只处理一个会话）、可见性（只有场景声明为可见的状态进聊天）。讨论任何一件时不要把另两件搅进来。
   - **术语**：用户可见的单位是**会话**，群里用本群自增的 `%n` 指代；会话内部为每个 agent 状态起的 paseo 运行单元叫 **agent**，对用户不可见（ARCHITECTURE §2）。
   - **指令两类**：程序指令不经过 AI；AI 指令要么新开会话，要么发到已有会话（`%n` 指定，省略则取引用消息所属会话、本群当前会话）。会话有完整的列表/详情/切换/重命名/归档指令。
   - bot 的全部 AI 业务都是场景：`/需求`、`/帮我调查`、`/解读` 是单对话态场景，`/升级bot` 是多状态场景；不为某个场景另起实现，也不把某个场景的细节写进调度本身。
4. **daemon 不会自己推进流程**。会话进入下一状态 = 当前 agent 的 AI 经回复报告 `transition` → bot 调度校验 → 需要时调度经 paseo CLI 起下一状态的 agent。
5. **无 MCP、无 WS**：bot → daemon 只走 paseo CLI shim。paseo 里的 agent 不用 MCP（pi 默认无 MCP）：CLI + skill + pi-unified-exec（`docker exec -it` / ssh）。跨容器调用走 `tools/` 下的 shim（`tgctl` / `paseoctl` / `deployctl`）。e2e、tg-cli 怎么用是场景状态 prompt 的事。
6. **容器拓扑固定**（ARCHITECTURE §1）：`deploy` 项目常驻 `caddy` `gateway` `astrbot` `snowluma` `paseo` `t2i`；`e2e`、`tg-cli` 是独立常驻的 compose 项目，永不随 `deploy` 下线；`e2e` 内不跑 pi、不是第二个 daemon。
7. **AI 工作目录**：无 `repo` 的场景，agent 在场景 `cwd`（如 `/workspace/openalice`）里；有 `repo` 的场景，agent 只在该会话从挂钩仓库 fixed-main 派生的 worktree 里。fixed-main（如 `/workspace/alicedev`）永不开 AI、永不写。
8. **部署只碰 astrbot 插件**：宿主 alicedev 检出切到 main 上的目标 SHA（交付 `bot/` 与 `templates/`）+ AstrBot 插件热重载；`requirements.txt` 或平台配置变化才 `restart astrbot`；paseo 不动。
9. **模型是部署配置**：paseo daemon 的 provider 配置文件决定；bot 只传 provider id。代码里不写死模型；换模型改一行配置。`/升级bot` 会话用 task:low 同款 `muse-spark-1.3-contributor`。**未经用户允许禁止用其他付费模型跑任何测试。**
10. **指令全部 DSL 化**：指令、消息路由、场景、用户可见的固定文字只写在 `templates/` 的 YAML DSL 里（ARCHITECTURE §3）；代码只实现封闭的动作、参数类型与卡片渲染。**禁止**在代码里写死指令、回话或场景；新增指令先看现有动作能否组合，确需新动作才加代码并同步 §3.2 的动作表。改完 DSL 必须跑 `python -m alicedev.dsl check` 并看 `dsl card` 出的图。

## 3. 验证规则

- **没有真实 e2e = 没做。** fake runner、stub、mock、单测、py_compile 只能辅助，不能当验收。
- `/升级bot` 的验收路径：真 daemon + 真 e2e/tg-cli 容器 + 真 AI 会话 + 真 QQ/TG 投递；场景的每个可见状态各真触发一次；证据（截图 / 日志 / 行）入 `docs/evidence/`。
- 隔离验证可以先于生产验证，但**隔离必须是同一架构**（bot 调度 → paseoctl shim → paseo CLI → daemon；agent 回复 → `/v1/reply` → 出站队列），在错架构上跑通的东西不算。
- 动生产（nekoringo2 `deploy` 项目、宿主 bot 检出、paseo 工作区、QQ 登录态）必须有明确闸门：先说清做什么、影响谁、如何回滚，再做。零改动的只读核查不需要闸门。

## 4. 委派与协作

- 主 session 负责设计、拆分、契约、验收；**不要自己逐文件手写**。多文件 / 多切片用 `task` 并行，`task:mid` / `task:low` 不限量，`task:high` 只给必须自定方案的切片。
- **先钉契约再委派**：跨切片接口（shim 签名、回复与 transition 载荷、场景定义格式、表结构、目录归属）写进 ARCHITECTURE.md 或一份 `local://` 契约后再派；不让子代理各自猜。
- 每个子代理任务必须写明：目标文件与非目标、逐步改动、可观察验收、**零生产改动**、跳过全仓 lint/test。同一文件只归一个子代理；共享文件先在 hub 对齐。
- 子代理**可能撒谎**：只认 artifact 与运行证据，不认"完成"二字。任务太大是撒谎主因，切小。
- 子代理报告的矛盾（"X 不支持"）先回到 §1 核对架构，不要下达"绕过它"的指令。
- 停止工作时先停掉所有 subagent（`hub cancel`），再汇报。

## 5. 工具与仓库习惯

- 文件读写用 `read` / `edit` / `write` / `grep` / `glob`，不用 shell 的 cat/sed/grep/find 替代；shell 只跑真实二进制（docker、ssh、git、tofu）。
- 服务器：`ssh -i ~/.ssh/dev-dai -o IdentitiesOnly=yes root@160.191.41.242`（nekoringo2）；compose 统一 `cd /srv/alicedev && docker compose -f deploy/docker-compose.yml --env-file deploy/.env …`。rsync 推送必须 `--exclude 'src/' 'backups/' 'deploy/.env' 'deploy/astrbot/*.rendered.json'`。
- DuckDB 单写者是 astrbot 进程；不要从别的进程打开 `alicedev.duckdb`。
- 凭据从 IaC/SOPS/`deploy/.env` 取，不粘进对话、不进 argv、不进 state。
- git：日常用 RiriAgent 账号；功能走 feature 分支，真 e2e 验收后再合 main；不拿 `git status/diff` 当验证。
- 图一律 mermaid。中文与用户沟通，术语保留英文。

## 6. 本轮当前工作（详见 HANDOFF.md §6）

1. **bot → daemon 控制面返工**：`bot/alicedev/paseo/mcp.py`（MCP，生产在用）与 `daemon_ws.py`（WS，未提交）都违背 §2.2，替换为 `tools/paseoctl` shim + CLI JSON 解析。
2. **出站改为消息队列**：`/v1/reply` 只入队（202），outbox worker 出队发送（ARCHITECTURE §6）。
3. **调度改为会话状态机，旧会话业务并入**：`bot/alicedev/upgrade/` 改造为 `bot/alicedev/scheduler/`，状态由回复 `transition` 推进；`templates/prompts/*` 迁为 `templates/scenarios/` 下的单对话态场景，`session_actor` 的建会话/当前指针/注入改由调度承担，现 `sessions` 表拆为会话（`sessions`，本群自增 `%n`）与 `agents`，`requirements` 表并入会话；补齐会话列表/详情/切换/重命名/归档指令；`/升级bot` 落为 `templates/scenarios/upgrade-bot/`（ARCHITECTURE §3/§7/§13）。
4. 建 alicedev fixed-main → e2e / tg-cli 上生产 → 宿主 bot 转 git 检出 → 真 e2e 验收 → 合 main。
5. **指令 DSL 化**：把 `commands/` 下写死的指令改为 `templates/commands/*.yaml` + `routes.yaml` + `messages.yaml`，代码收敛为 `alicedev/dsl/`（加载、校验、check/card CLI）与 `alicedev/actions/`（封闭动作集合）；帮助卡、指令卡、会话卡从 DSL 生成；`templates/` 改由 `deployctl` 检出交付，nekoringo-iac `apps/alicedev` 的托管集去掉 `templates/`（ARCHITECTURE §0.4/§3/§11）。
6. **去掉 paseo fork**：paseo 镜像改为上游原样 + `deploy/paseo/Dockerfile` 运行层；分享视图（只留 agent 操作区）改由网关注入样式实现（ARCHITECTURE §8），`/链接` 目标去掉 `embed=1`；`paseo-alicedev` 里 upgrade-bot 相关内容按 HANDOFF §5 摘走后不再使用该仓库；清理本机临时 daemon / lab / 容器（HANDOFF §4）。
