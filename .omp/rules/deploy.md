---
description: 部署 alicedev 到 nekoringo2 生产（bot/templates 上线、镜像构建、IaC、paseo 升级、回滚）或排查生产宿主时必读
alwaysApply: false
---

# 部署规则（nekoringo2）

部署契约（容器、卷、交付范围）见 `ARCHITECTURE.md` §11；工具参数见 `tools/README.md`；登录、面板等部署以外的宿主操作见 `docs/runbook.md`。本文规定上线怎么做、什么不许做。

## 1. 硬规则

1. **闸门**：动生产（`deploy` 项目、宿主检出、paseo 工作区、镜像、QQ/TG 登录态）前，先说清做什么、影响谁、如何回滚，再做。零改动的只读核查不需要闸门。
2. **线上停在 main**：线上的 bot 检出、镜像源与 IaC 的 `alicedev_revision` 都是 main 上已合入的提交，用完整 40 位 SHA（短 SHA 在 fetch 时找不到 ref）。唯一例外是合并前的生产验收（AGENTS §3）：只能在生产上验证的改动（真 AI、真 daemon、宿主上的容器配置），可以把已推送的功能分支完整 SHA 部署到生产做验收；通过就合 main，并把线上切到合并后的 main SHA；不通过就按 §7 回滚，线上不长期停在功能分支上。
3. **一类改动一条路径**：按 §4 的表上线。不手工拷文件，不在宿主检出里改文件；`deployctl apply/run/rollback` 只以 uid 1000 运行（在 paseo 容器里以 `paseo` 用户跑，或经 `deployrun`），root 跑会留下 owner 改写不了的 git 文件。
4. **bot 部署不动 paseo**：`bot/`、`templates/` 上线只切检出并热重载插件；paseo 镜像只在 §4 对应行更新。
5. **先打回滚标签再构建**：构建任何镜像前，给当前镜像打 `:rollback-<tag>`。
6. **先体检再部署**（§3）。宿主 `/tmp` 是约 3.9G 的 tmpfs，占用的是内存（8G 内存、无 swap）；`/tmp` 写满后 runc 无法 `docker exec`：所有 healthcheck 失败，bot 的 `paseoctl` shim 失效，`deployrun` 也跑不起来。
7. **宿主上不留临时进程和大文件**：抓包、构建产物、下载的安装包不写 `/tmp`，用完即停、即删。删掉仍被进程打开的文件不会释放空间，要停掉进程。2026-09-25 的事故就是这样：一个孤儿 `tcpdump -w /tmp/…pcap` 跑了 5 天，写满 `/tmp`，全部容器 unhealthy。
8. **核验后才算上线**（§5）。只看到命令退出码为 0 不算上线。
9. **凭据**只来自 SOPS → IaC 落地的 `/srv/alicedev/deploy/.env`；不粘进对话、不进 argv。

## 2. 宿主布局与所有权

| 路径 | 内容 | 所有者 |
|---|---|---|
| `/srv/alicedev/deploy/` | compose、Caddyfile、astrbot 配置模板与渲染产物、`.env` | IaC（`nekoringo-iac/apps/alicedev`） |
| `/srv/alicedev/app/` | alicedev git 检出：astrbot 挂 `bot/`、`templates/`；paseo 挂到 `/deploy/app`；镜像构建源 | `tools/deployctl`（uid 1000） |
| `/srv/alicedev/e2e-src/` | 常驻 e2e 栈挂载的独立检出 | `tools/e2e_driver.py`（uid 1000） |
| `/srv/alicedev/src/paseo/` | `mouriya-s-lab/paseo` 源码（`PASEO_SRC_REF`），只用于构建 paseo 基础镜像 | `make paseo-src` |
| `/srv/alicedev/backups/` | 手工备份 | 运维 |

SSH：`ssh -i ~/.ssh/dev-dai -o IdentitiesOnly=yes root@160.191.41.242`。宿主上的 `make` 目标在 `/srv/alicedev/app` 下执行（`make -C /srv/alicedev/app <target>`）；compose 统一用 `docker compose -f /srv/alicedev/deploy/docker-compose.yml --env-file /srv/alicedev/deploy/.env …`。宿主检出属 uid 1000，root 下读它的 git 信息要加 `git -c safe.directory=/srv/alicedev/app …`。

## 3. 部署前体检

```sh
df -h / /tmp                      # /tmp 接近满：先找占用者（见下）
free -m                           # available 过低：同样先查 /tmp（tmpfs 算 shared 内存）
docker ps --format '{{.Names}}\t{{.Status}}'   # deploy 与 e2e、tg-cli 项目应全部 healthy（snowluma 无 healthcheck）
docker exec -u paseo alicedev-paseo /deploy/app/tools/deployctl status --app /deploy/app
```

`deployctl status` 的 `head`、`revision_file`、`health.revision` 应一致，等于当前线上 SHA。

`/tmp` 的 `df` 占用远大于 `du -sh /tmp` 时，是已删除但仍被打开的文件：遍历 `/proc/*/fd` 找指向 `/tmp/… (deleted)` 的大文件，确认进程来历后停掉它（过闸门）。体检不过不部署。

## 4. 按改动类型上线

| 改了什么 | 怎么上线 |
|---|---|
| `bot/`、`templates/` | 正常路径是 `/升级bot`（批准即 merge，`deploying` 状态自动部署）。手工：`docker exec -u paseo alicedev-paseo /deploy/app/tools/deployrun --app /deploy/app --commit <main 完整 SHA>`；核验失败自动切回并重载 |
| `bot/requirements.txt` | `deployrun` 自动改为 `docker restart alicedev-astrbot`；长期依赖同时进 `deploy/astrbot/Dockerfile` 预装，再按下面 Dockerfile 那一行重建 |
| `deploy/`（compose、astrbot 配置） | 提交 → IaC apply（§6）→ `make -C /srv/alicedev/app up` |
| `deploy/paseo`、`deploy/astrbot` Dockerfile、`harness/` | 宿主检出切到新 SHA（`deployctl apply`）→ 打回滚标签 → `make -C /srv/alicedev/app build` → `make -C /srv/alicedev/app up` |
| `gateway/` | 宿主检出切到新 SHA（同时改了 `bot/` 时，先按第一行跑 `deployrun`，它会一并切检出）→ `docker tag` 当前镜像为 `alicedev/gateway:rollback-<tag>` → `docker compose … build gateway` → `docker compose … up -d --no-deps gateway`。不动 paseo；重启网关会使尚未兑换的一次性链接失效 |
| paseo 上游版本 | 改 `PASEO_SRC_REF`（SOPS）→ IaC apply（§6）→ `make paseo-src` → 打回滚标签 → `build` → `up` → 按 §5 重新核对分享视图 |

### 首次搭建（或全部重建）

在开发机的 alicedev 检出里：

```sh
make paseo-src                                 # 传上游 paseo 源码到 /srv/alicedev/src/paseo
make bootstrap-app COMMIT=<main sha>           # 克隆 /srv/alicedev/app 并写 bot/REVISION
make bootstrap-e2e-src COMMIT=<main sha>       # 克隆 /srv/alicedev/e2e-src
```

这三步必须在 `up` 之前完成，否则 docker 会把不存在的挂载点建成 root 所有的空目录。然后在宿主上：

```sh
make -C /srv/alicedev/app build     # paseo 基础镜像（前端构建耗内存，先加临时 swap；不要放在 /tmp）、paseo/astrbot 子镜像、gateway
make -C /srv/alicedev/app up
make -C /srv/alicedev/app e2e-up    # 依赖 up 创建的 alicedev-e2e 网络
make -C /srv/alicedev/app tg-up
make -C /srv/alicedev/app fixed-main
```

## 5. 部署后核验

每次上线都做：

1. `deployctl status`（§3）的 `health.revision` 等于目标 SHA。
2. astrbot 日志出现 `alicedev initialized: … 0 DSL errors`。
3. bot 能驱动 paseo：`docker exec alicedev-astrbot docker exec -u paseo alicedev-paseo paseo ls --json` 返回 JSON。
4. 真实入口有回话：`docker exec alicedev-tg-cli tg send "RIRI OuO" "/alicedev"`，约 20 秒后 `tg sync "RIRI OuO"`、`tg recent --chat "RIRI OuO" --yaml`，确认 bot 回了一条新消息（帮助卡是图片，`content` 为空）。

按改动类型追加：

- 改了行为（指令、场景、回话）：在 TG 上真实触发改动涉及的每条路径，证据写进对应 PR 的正文（图片经 image-share）。
- `gateway/` 或 paseo 上游：用 agent-browser 在新浏览器里打开一次性链接，走完兑换 → 会话页 → 进入会话。paseo 升级后还要核对 `gateway/static/paseo-view.css` 的选择器，以及 `gateway/static/paseo-boot.js` 依赖的存储键名和路由。验收标准是首次打开、刷新、前进、后退都停在工作区，且时间线能加载。

## 6. 凭据与 IaC

凭据全部在 `nekoringo-iac/_shared/secrets/secrets.sops.yaml` 的 `alicedev_deploy_env_b64` 里（整份 `.env` 的 base64），由 IaC 落到 `/srv/alicedev/deploy/.env`（0600）。键见 `deploy/.env.example`。其中：

- `GITHUB_TOKEN`：RiriAgent token，供宿主检出 fetch、paseo 里 agent 的 git/gh 使用。
- `ASTRBOT_API_KEY`：AstrBot 插件权限 API key，`deployctl` 热重载时用它认证。生成方式：在 astrbot 容器内调用 AstrBot 自己的 `ApiKeyService.create_api_key({"name": "deployctl", "scopes": ["plugin"]})`（作用于 `/AstrBot/data/data_v4.db`），只把 `abk_…` 写进 root-only 文件，再写入 SOPS。AstrBot 的日志也写 stdout，不要把整段输出当成 key。
- `E2E_DASHBOARD_PASSWORD`：e2e 栈 WebChat 的登录密码。

改凭据：`sops -d --extract '["alicedev_deploy_env_b64"]' … | base64 -d` 解出、修改、`base64` 编码后用 `sops --set` 写回，提交 nekoringo-iac，再 apply：

```sh
cd ~/Ext/code/nekoringo-iac/apps/alicedev
# terraform.tfvars: alicedev_revision = <已提交的 alicedev SHA>
tofu plan && tofu apply
```

`apply` 只落 `deploy/` 和 `.env`，不启停任何容器；改了 compose 或 astrbot 配置时，接着 `make up`。

## 7. 备份与回滚

- 备份：DuckDB 在 `alicedev_astrbot_data` 卷的 `/AstrBot/data/plugin_data/alicedev/alicedev.duckdb`（用 `docker cp` 复制，连同 `.wal`）。不要从 astrbot 以外的进程打开它。paseo 状态在 `alicedev_paseo_home` 卷：`docker run --rm -v alicedev_paseo_home:/v:ro -v <dir>:/b alpine tar -C /v -czf /b/paseo_home.tgz .`。
- bot/DSL 回滚：`deployrun` 失败会自动切回；手工回滚用 `deployctl rollback`（见 `tools/README.md`）。
- 镜像回滚：把 `:rollback-<tag>` 标签打回运行标签，再 `make up`。
