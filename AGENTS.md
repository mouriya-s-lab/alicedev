# AGENTS.md — alicedev 开发阶段规则

适用于在本仓库工作的每个 agent（主 session 与子代理）。顺序：先读本文件，再 `ARCHITECTURE.md`（契约），再 `HANDOFF.md`（状态与待办）。

## 1. 先对齐，再动手

- **契约变更先改 `ARCHITECTURE.md`，再写代码。** 用户口头定下的设计决定必须当场落到该文件；不落文件的决定视为不存在。
- 开工前对照 ARCHITECTURE.md §0 三条不变量与 §1 容器/工作目录表自查；任何一条对不上就停下改文档或问，不要顺着错前提往下建。
- 遇到和文档矛盾的现象（API 不支持、模型不支持、路径不存在），**先当作"我在错的层面干活"的信号**，回到架构核对；不要当局部 bug 打补丁。
- 只检查自己是不够的：核对**环境事实** —— 有几个容器、AI 工作目录在哪、有几个 project、daemon 的真实 API 长什么样。这些都在 `deploy/docker-compose.yml`、paseo `/workspace` 卷、`paseo-alicedev/packages/{client,protocol}` 里，不靠记忆。

## 2. 架构硬约束（违背即返工）

1. **monorepo**：所有 alicedev 代码只在本仓库。`paseo-alicedev` fork 只允许 embed 模式与 Arch 镜像（ARCHITECTURE §9），**禁止**往里放调度、skill、schema、工具或任何业务逻辑。
2. **paseo daemon 是已部署、不可修改的基础设施**。它被动响应 API：起会话、等完成、查状态、建/归档 worktree。它通过 RPC 驱动各 harness（omp / pi / claude / codex）并自行整理会话产出与状态。
   - bot **只调 daemon API、只读它整理好的结果**。
   - **禁止**直接驱动 harness（`paseo run`、`pi`、`omp` 命令行）、**禁止**要求模型按 schema 吐 JSON、**禁止**解析模型自由文本当状态、**禁止**在 paseo home 里临时改 provider。
   - omp 与 pi 是两套不同的 harness（omp 建在 pi 底层之上），不可互换；本 session 自己跑在 omp 里，paseo 里的会话按 daemon provider 配置跑。
3. **bot 侧三件事分开**：传输（平台适配器 ↔ QQ/TG 收发）、调度（驱动 daemon 推进流程的程序）、可见性（内部状态不进聊天，只有边界事件进聊天）。讨论任何一件时不要把另两件搅进来。
4. **daemon 不会自己推进流程**。"从实现进入测试"永远是 bot 侧调度程序判断上一会话完成后再调一次 daemon 起下一会话。
5. **paseo 里的会话不用 MCP**（pi 默认无 MCP）：CLI + skill + pi-unified-exec（`docker exec -it` / ssh）。跨容器调用走 `tools/` 下的 shim。
6. **容器拓扑固定**（ARCHITECTURE §1）：`caddy` `gateway` `astrbot` `snowluma` `paseo` `t2i` `e2e` `tg-cli` 常驻；`e2e` 内不跑 pi、不是第二个 daemon；`e2e` 与 `tg-cli` 独立常驻、永不随其他服务下线。
7. **AI 工作目录**：普通会话 `/workspace/openalice`；`/升级bot` 会话只在该需求从 fixed-main 派生的 worktree 里。fixed-main `/workspace/alicedev` 永不开 AI、永不写。
8. **部署只碰 bot**：`git checkout <commit>` + `restart astrbot`；paseo 不动。`/srv/alicedev/bot` 是 git 检出。
9. **模型是部署配置**：paseo daemon 的 provider 配置文件决定；bot 只传 provider id。代码里不写死模型；换模型改一行配置。`/升级bot` 会话用 task:low 同款 `muse-spark-1.3-contributor`。**未经用户允许禁止用其他付费模型跑任何测试。**

## 3. 验证规则

- **没有真实 e2e = 没做。** fake runner、stub、mock、单测、py_compile 只能辅助，不能当验收。
- `/升级bot` 的验收路径：真 daemon + 真 e2e/tg-cli 容器 + 真 AI 会话 + 真 QQ/TG 投递；四类边界事件各真触发一次；证据（截图 / 日志 / 行）入 `docs/evidence/`。
- 隔离验证可以先于生产验证，但**隔离必须是同一架构**（bot 侧调度 → daemon API），在错架构上跑通的东西不算。
- before/after 证据图必须在同一 e2e 容器、同一冻结场景、走真实用户面；渲染前清 t2i / 渲染缓存。
- 动生产（nekoringo2 `deploy` 项目、`/srv/alicedev/bot`、paseo 工作区、QQ 登录态）必须有明确闸门：先说清做什么、影响谁、如何回滚，再做。零改动的只读核查不需要闸门。

## 4. 委派与协作

- 主 session 负责设计、拆分、契约、验收；**不要自己逐文件手写**。多文件 / 多切片用 `task` 并行，`task:mid` / `task:low` 不限量，`task:high` 只给必须自定方案的切片。
- **先钉契约再委派**：跨切片接口（shim 签名、事件载荷、表结构、目录归属）写进 ARCHITECTURE.md 或一份 `local://` 契约后再派；不让子代理各自猜。
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

1. QQ 协议端 NapCat → snowluma（bot 没写完，这是现在的事）。
2. 确认 daemon API → 建迭代 bot project / fixed-main → 调度程序从 paseo fork 迁回 `bot/alicedev/upgrade/` 并改为 daemon API 驱动 → e2e / tg-cli 上生产 → bot 目录转 git → 真 e2e 验收 → 合 main。
3. 清理 paseo fork 里放错的东西与本机临时 daemon / lab / 容器（HANDOFF §4）。
