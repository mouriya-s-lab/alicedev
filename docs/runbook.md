# alicedev 运维手册（nekoringo2）

契约见 `ARCHITECTURE.md`；各工具的参数与细节见 `tools/README.md`。本文只写宿主上的操作顺序。

## 1. 宿主布局与所有权

| 路径 | 内容 | 所有者 |
|---|---|---|
| `/srv/alicedev/deploy/` | compose、Caddyfile、astrbot 配置模板与渲染产物、`.env` | IaC（`nekoringo-iac/apps/alicedev`） |
| `/srv/alicedev/app/` | alicedev git 检出：astrbot 挂 `bot/`、`templates/`；paseo 挂到 `/deploy/app`；镜像构建源 | `tools/deployctl`（uid 1000） |
| `/srv/alicedev/e2e-src/` | 常驻 e2e 栈挂载的独立检出 | `tools/e2e_driver.py`（uid 1000） |
| `/srv/alicedev/src/paseo/` | `mouriya-s-lab/paseo` 源码（`PASEO_SRC_REF`），只用于构建 paseo 基础镜像 | `make paseo-src` |
| `/srv/alicedev/backups/` | 手工备份 | 运维 |

SSH：`ssh -i ~/.ssh/dev-dai -o IdentitiesOnly=yes root@160.191.41.242`。宿主上的 `make` 目标都在 `/srv/alicedev/app` 下执行（`make -C /srv/alicedev/app <target>`），compose 统一用 `/srv/alicedev/deploy/docker-compose.yml` 与 `/srv/alicedev/deploy/.env`。

## 2. 凭据

全部在 `nekoringo-iac/_shared/secrets/secrets.sops.yaml` 的 `alicedev_deploy_env_b64`（整份 `.env` 的 base64），由 IaC 落到 `/srv/alicedev/deploy/.env`（0600）。键见 `deploy/.env.example`。其中：

- `GITHUB_TOKEN`：RiriAgent token，供宿主检出 fetch、paseo 里 agent 的 git/gh。
- `ASTRBOT_API_KEY`：AstrBot 插件权限 API key，供 `deployctl` 热重载。生成方式：在 astrbot 容器内调用 AstrBot 自己的 `ApiKeyService.create_api_key({"name": "deployctl", "scopes": ["plugin"]})`（作用于 `/AstrBot/data/data_v4.db`），只把 `abk_…` 写进 root-only 文件再写入 SOPS；注意 AstrBot 的日志也写 stdout，不要把整段输出当 key。
- `E2E_DASHBOARD_PASSWORD`：e2e 栈 WebChat 登录密码。

改凭据：`sops -d --extract '["alicedev_deploy_env_b64"]' … | base64 -d` 解出、修改、`base64` 后用 `sops --set` 写回，提交 nekoringo-iac，再做 §3。

## 3. 宿主文件（IaC）

```sh
cd ~/Ext/code/nekoringo-iac/apps/alicedev
# terraform.tfvars: alicedev_revision = <已提交的 alicedev SHA>
tofu plan && tofu apply
```

`apply` 只落 `deploy/` 与 `.env`，不启停任何容器。改了 compose 或 astrbot 配置后，接着做 §5 的 `make up`。

## 4. 首次搭建（或全部重建）

在开发机的 alicedev 检出里：

```sh
make paseo-src                                 # 传上游 paseo 源码到 /srv/alicedev/src/paseo
make bootstrap-app COMMIT=<main sha>           # 克隆 /srv/alicedev/app 并写 bot/REVISION
make bootstrap-e2e-src COMMIT=<main sha>       # 克隆 /srv/alicedev/e2e-src
```

必须在 `up` 之前完成，否则 docker 会把不存在的挂载点建成 root 所有的空目录。然后在宿主上：

```sh
make -C /srv/alicedev/app build     # paseo 基础镜像（前端构建耗内存，4 核 8G 无 swap 时先加临时 swap）、paseo/astrbot 子镜像、gateway
make -C /srv/alicedev/app up
make -C /srv/alicedev/app e2e-up    # 依赖 up 创建的 alicedev-e2e 网络
make -C /srv/alicedev/app tg-up
make -C /srv/alicedev/app fixed-main
```

验证：`docker exec alicedev-astrbot docker exec -u paseo alicedev-paseo paseo ls --json` 能返回；astrbot 日志出现 `alicedev initialized: … 0 DSL errors`；`/v1/health` 的 `revision` 等于检出 SHA。

## 5. 日常变更

| 变更了什么 | 怎么上线 |
|---|---|
| `bot/`、`templates/` | 正常路径是 `/升级bot`（批准即 merge，`deploying` 状态自动部署）。手工：`docker exec -u paseo alicedev-paseo /deploy/app/tools/deployrun --app /deploy/app --commit <main sha>`，失败自动切回并重载 |
| `bot/requirements.txt` | `deployrun` 自动改为 `docker restart alicedev-astrbot`；长期依赖同时进 `deploy/astrbot/Dockerfile` 预装，需 §5 下一行 |
| `deploy/`（compose、astrbot 配置） | 提交 → §3 IaC apply → `make -C /srv/alicedev/app up` |
| `deploy/paseo`、`deploy/astrbot` Dockerfile、`harness/`、`gateway/` | 宿主检出切到新 SHA（`deployctl apply`）→ `make -C /srv/alicedev/app build` → `make up` |
| paseo 上游版本 | 改 `PASEO_SRC_REF`（SOPS）→ §3 → `make paseo-src` → `build` → `up`；之后用 agent-browser 重新核对分享视图的选择器（`gateway/static/paseo-view.css`） |

## 6. Telegram（tg-cli）

`alicedev-tg-cli` 用自己的独立授权（卷 `alicedev_tg_cli_data`）。登录：宿主上 `docker exec -i alicedev-tg-cli python /opt/alicedev/tg-qr-login.py --timeout 180` 打出 `tg://login?token=…`，开发机上用已登录的本机会话批准：`~/.local/share/uv/tools/kabi-tg-cli/bin/python tools/tg-approve-login '<url>'`。验收用的私聊名是 `RIRI OuO`（bot `@ririOuObot`）：`docker exec alicedev-tg-cli tg send "RIRI OuO" "/alicedev"`，`tg sync "RIRI OuO"` 后 `tg recent --chat "RIRI OuO" --yaml`。

## 7. QQ（snowluma）

登录态与设备身份在 `qq-gateway-data` / `qq-client-config` / `qq-client-data` 卷。首次登录是人工步骤：打开 `http://<PANEL_BIND_IP>:6081/`（netbird 内），远程桌面密码见 `docker logs alicedev-snowluma | grep 'remote desktop password'`，桌面内扫码；扫码后 `docker restart alicedev-astrbot` 让 aiocqhttp 适配器重连。

## 8. AstrBot dashboard

只绑 `PANEL_BIND_IP:6185`（netbird 内），或 `ssh -N -L 16185:127.0.0.1:6185` 隧道。用户名 `astrbot`。

## 9. 备份与回滚

- 备份：DuckDB 在 `alicedev_astrbot_data` 卷 `/AstrBot/data/plugin_data/alicedev/alicedev.duckdb`（`docker cp` 复制，连同 `.wal`）；paseo 状态在 `alicedev_paseo_home` 卷（`docker run --rm -v alicedev_paseo_home:/v:ro -v <dir>:/b alpine tar -C /v -czf /b/paseo_home.tgz .`）。
- bot/DSL 回滚：`deployrun` 失败会自动切回；手工 `deployctl rollback`（见 `tools/README.md`）。
- 镜像回滚：构建新镜像前给当前镜像打 `:rollback-<tag>` 标签，出问题时改回标签再 `make up`。

## 10. 已知限制

- AstrBot 主动发消息拿不到平台消息 id：引用 bot 消息时按消息首行的 `%n` 标记找会话。
- paseo 0.8.0 对 idle agent 执行 `stop` 是空操作，12h 空闲关闭只记在 `agents.status`。
- 分享视图：上游 paseo 前端存在首次加载时 host 未注册就解析路由（落到 `/open-project`）与自托管连接重复探测（对话区显示 `Subscription released`、一直加载）两个问题，见 HANDOFF。
