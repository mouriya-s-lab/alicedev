# alicedev 部署运行手册

本文是 `nekoringo2` 上实际部署状态的记录，也是首装、升级和排障手册。
生产 Compose 只把 AstrBot dashboard 绑定到 `127.0.0.1:6185`；公网入口只有
Caddy 的 80/443，Caddy 再转发到内部 gateway。不要把 paseo、gateway、AstrBot
内部 API 或 t2i 端口发布到主机。
Compose 还会启动两个短命 helper：`reports-init` 在共享卷中创建 `_published`
子目录，并把报告卷 chown 到 1000:1000（容器内 `paseo` 用户），确保 gateway
的只读 subpath mount 在首次启动时存在且 agent 可写报告；`astrbot-init` 在每次
`up` 时把渲染后的 `cmd_config.rendered.json` / `alicedev_config.rendered.json`
复制进 `astrbot_data` 卷（此前只读单文件 bind mount 会在 AstrBot 重写配置时
触发 EBUSY，故改用卷内复制）。

## 1. 事实与边界

- `nekoringo2` 当前为 Debian 13、x86_64，Docker Engine/Compose 版本为
  29.8.1/v5.5.1；这些版本和容量事实见
  [`docs/research/infra-facts.md:5-74`](research/infra-facts.md)。
- 研究时主机 TCP 80/443 没有监听，且没有现存 Compose 项目
  ([`docs/research/infra-facts.md:76-101`](research/infra-facts.md))。部署前仍要重新检查，
  因为端口占用会随时间变化。
- `alicedev.237575.xyz` 的 A 记录当前是手工记录，指向 `160.191.41.242`
  ([`HANDOFF.md:80`](../HANDOFF.md))。DNS 迁移到 IaC 的 follow-up issue 文本见 §4，
  本手册不直接改 DNS。
- paseo 的 `paseo_home` 是用户自管的持久化卷；其中的 omp 凭据不进 git、不进
  `.env.example`，也不由 Compose 生成。PASEO 密码必须始终非空。

### 1.1 凭据规则

凭据从已有 secret store/IaC 或管理员受控的部署环境取得，不在聊天、issue、日志
或 git 中粘贴。`deploy/.env` 仅存在于部署主机并设为 `0600`；
`deploy/.env.example` 只有占位值。Telegram token 通过渲染后的 AstrBot
`cmd_config.json` 使用，GitHub token 只在需要认证的 GitHub 预取时提供。

## 2. 首次部署

代码交付走从运维 Mac 的 rsync，不在服务端做版本库检出（两个仓库都是私有库，
服务端没有 deploy key）。alicedev 运行树在服务端的 `/srv/alicedev`；`src` 下
只保留 `paseo-alicedev` 一个检出（Compose 构建 Arch 基础镜像的
`PASEO_REPO_CONTEXT`），不存在 `/srv/alicedev/src/alicedev`。

### 2.1 主机预检查与镜像记录

```bash
ssh nekoringo2 'mkdir -p /srv/alicedev/src && hostname && uname -m && docker version --format "client={{.Client.Version}} server={{.Server.Version}}" && docker compose version'
ssh nekoringo2 'ss -tlnp | grep -E ":(80|443)\\b" || true'
```

提前拉取并记录生产使用的官方镜像。这里的 digest 是 `deploy/docker-compose.yml`
中的 amd64 pin；按 digest 引用检查镜像，因为只用 tag 的 `docker image inspect`
在 digest-only pull 后可能找不到本地 tag：

```bash
ssh nekoringo2 'docker pull caddy:2-alpine@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0'
ssh nekoringo2 'docker pull soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf'
ssh nekoringo2 'docker pull soulter/astrbot-t2i-service:latest@sha256:f7243fd37cc247f13da2b23eb394e77cae18ee7b104440ee776d55419e89f61e'
ssh nekoringo2 'for ref in caddy:2-alpine@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0 soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf soulter/astrbot-t2i-service:latest@sha256:f7243fd37cc247f13da2b23eb394e77cae18ee7b104440ee776d55419e89f61e; do docker image inspect --format=\"{{.Id}} {{.RepoDigests}}\" \"$ref\"; done'
```

若镜像 registry 或 digest 发生变化，先更新 Compose pin 和本节记录，再继续；不要
为了“先跑起来”改成无 pin 的 `latest`。

### 2.2 同步源码（rsync 推送）

先在 Mac 上跑 `make harness`（`harness/dist` 产物随同步发往服务端，不在服务端
构建），记下两个本地检出的 commit，再同步：

```bash
make harness
git rev-parse HEAD
git -C /Users/mouriya/Ext/code/paseo-alicedev rev-parse HEAD
rsync -az --delete --exclude '.git/' --exclude 'node_modules/' --exclude 'harness/node_modules/' --exclude 'deploy/.env' --exclude 'deploy/astrbot/*.rendered.json' /Users/mouriya/Ext/code/alicedev/ nekoringo2:/srv/alicedev/
ssh nekoringo2 mkdir -p /srv/alicedev/src/paseo-alicedev && rsync -az --delete --exclude '.git/' --exclude 'node_modules/' /Users/mouriya/Ext/code/paseo-alicedev/ nekoringo2:/srv/alicedev/src/paseo-alicedev/
```

说明：`deploy/.env` 和 `deploy/astrbot/*.rendered.json` 只存在于服务端，
同步时排除，不会被 Mac 侧覆盖。升级时先在 Mac 检出审阅 commit，记下同步时刻的
`git rev-parse HEAD`（即审计依据），再走同一组 rsync；不要把
`src/paseo-alicedev` 当作可删除的 build cache。

### 2.3 环境、渲染和构建

首装时才从模板创建服务端 `deploy/.env`（`0600`），之后升级走 §2.2 的 rsync
（`.env` 被排除，不会被覆盖）：

```bash
ssh nekoringo2 'cd /srv/alicedev && cp deploy/.env.example deploy/.env && chmod 600 deploy/.env'
# 在受控终端编辑 deploy/.env；填入秘密，但不要把文件内容输出到日志。
ssh -tt nekoringo2 'cd /srv/alicedev && ${EDITOR:-vi} deploy/.env'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet'
```

服务端 `deploy/.env` 中 `PASEO_REPO_CONTEXT=../src/paseo-alicedev`（指向 §2.2
同步来的检出）。所有 Compose 调用统一从 `/srv/alicedev` 发起：
`docker compose -f deploy/docker-compose.yml --env-file deploy/.env ...`
（与 `make build`、`make up`、`make workspace-init` 等价）。

`make render-config` 会把 `deploy/astrbot/cmd_config.json` 和
`deploy/astrbot/alicedev_config.json` 渲染成仅存在于部署工作树的
`*.rendered.json`。前者预置 Telegram、WebChat 和 `t2i_endpoint=http://t2i:8999`；
后者把 paseo/gateway/报告卷和 `/AstrBot/alicedev-templates` 传给插件。模板
文件使用 JSON 安全转义，不能用手工 `sed` 替换密码。`astrbot-init` 在每次 `up`
时把渲染产物复制进 `astrbot_data` 卷。

镜像构建分两步：先由 Compose 从 `PASEO_REPO_CONTEXT` 的
`docker/arch/Dockerfile` 构建基础镜像 `alicedev/paseo-arch:local`（build args：
`EXPO_PUBLIC_PASEO_SELFHOSTED=true`、`EXPO_PUBLIC_LOCAL_DAEMON=self-hosted`、
`PASEO_VERSION=0.8.0`、`OMP_VERSION=18.2.5`；服务端完整构建约 20 分钟），再构建
alicedev paseo child image `alicedev/paseo:local`（`deploy/paseo/Dockerfile`，
COPY §2.2 随同步发来的 `harness/dist` 产物），以及 `alicedev/gateway:local`
（只改 gateway 时 `docker compose ... build gateway && ... up -d gateway`，
约 15 秒）。构建和启动：

```bash
ssh nekoringo2 'cd /srv/alicedev && make build'
ssh nekoringo2 'cd /srv/alicedev && make up'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps'
```

当前服务：`caddy`、`gateway`、`astrbot`、`t2i`、`paseo` 全部 healthy，
另有两个一次性 helper：`reports-init`、`astrbot-init`。其中 astrbot 和 paseo
还接入 `edge` 网络，用于出站访问（LLM API、GitHub）。

首次启动后再初始化 workspace 卷：`make workspace-init` 把 OpenAlice 克隆到
workspace 卷的 `/workspace/openalice`（可重复执行，已存在时跳过克隆）：

```bash
ssh nekoringo2 'cd /srv/alicedev && make workspace-init'
```

`make up` 不会重新 build；源码或 harness 变化后先走 §2.2 的 rsync，再跑
`make build`。`make down` 不删除卷，避免误删会话、omp 凭据、workspace 和报告。

轮换 `GATEWAY_SECRET`、`ALICEDEV_INTERNAL_TOKEN`、`PASEO_PASSWORD`
（都只在服务端的 `deploy/.env`，`0600`）后，必须重跑 `make render-config`
再 `docker compose ... up -d`（`astrbot-init` 会重新播种配置）。轮换
`GATEWAY_SECRET` 会让已发分享 cookie 失效；重启 gateway 会让未消费的一次性
token 失效。

## 3. Caddy 与 TLS 首装验收

Caddy 生产配置 (`deploy/Caddyfile`) 只反代 `gateway:8080`。首次接入或 DNS/防火墙
变更时，先**只启动临时 Caddy**，用 `respond 200` 证明外部 HTTP-01 和证书签发，
不启动 AstrBot、gateway、paseo 或 t2i。临时数据目录不要复用生产 `caddy_data`。

```bash
ssh nekoringo2 'mkdir -p /srv/alicedev/acme-smoke/data /srv/alicedev/acme-smoke/config'
ssh nekoringo2 'cat > /srv/alicedev/acme-smoke/Caddyfile <<"EOF"
alicedev.237575.xyz {
    respond "alicedev ACME smoke" 200
}
EOF'
ssh nekoringo2 'docker rm -f alicedev-caddy-acme 2>/dev/null || true'
ssh nekoringo2 'docker run -d --name alicedev-caddy-acme --restart=no -p 80:80 -p 443:443 -v /srv/alicedev/acme-smoke/Caddyfile:/etc/caddy/Caddyfile:ro -v /srv/alicedev/acme-smoke/data:/data -v /srv/alicedev/acme-smoke/config:/config caddy:2-alpine@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0 caddy run --config /etc/caddy/Caddyfile --adapter caddyfile'
ssh nekoringo2 'docker logs --since=2m alicedev-caddy-acme'
# Run this from a client outside nekoringo2, not from the container host only.
curl -vI --max-time 30 https://alicedev.237575.xyz/
ssh nekoringo2 'docker inspect --format "{{.State.Status}}" alicedev-caddy-acme && docker rm -f alicedev-caddy-acme'
```

验收记录必须包含：`curl -vI` 的 `HTTP/2 200`、证书 `issuer`（从 verbose TLS
段读取）、Caddy 日志中成功取得证书的行，以及临时容器已删除。若 80/443 被占用、
A 记录不再指向主机、外部网络不可达或 ACME rate limit 触发，停止验收并修复其
基础设施前置条件；不要启动其他服务绕过这一步。

生产 Caddy 启动后，外部入口应为 `https://${ALICEDEV_HOST}/`；gateway 的
`/_alicedev/health` 只用于内部 healthcheck，正常用户流量仍须经过 gateway 的
cookie/token 策略。

## 4. DNS 后续 IaC issue（只记录，不在本手册执行）

当前记录由人工创建，后续应由 `pve-vctcn/apps/dns` 接管。建议 issue body：

```markdown
# 将 alicedev.237575.xyz 纳入 pve-vctcn/apps/dns

## 当前事实
- app repo: mouriya-s-lab/alicedev
- 主机: nekoringo2 / 160.191.41.242
- 当前记录: alicedev.237575.xyz A 160.191.41.242，DNS-only，曾由运维手工创建
- 依据: alicedev `HANDOFF.md` §7；`docs/research/infra-facts.md` §2

## 目标
在 `pve-vctcn/apps/dns` 的 237575.xyz 声明中表达该记录，完成 preview/apply，
并删除或明确标记手工记录，避免 Terraform drift 和重复记录。

## 验收
- preview 只包含预期的 alicedev 记录变更。
- apply 后权威 DNS 返回唯一 A 记录 160.191.41.242。
- 从外部解析和 HTTPS 请求验证 Caddy 入口仍可达。

## 不在本 issue
VM/CT placement、Docker stack、应用 secrets、Caddy 配置和 QQ 协议端。
```

## 5. paseo provider、omp 凭据与插件 reload

Compose 只把 `omp` 和 `omp-alicedev` provider 配置装入 paseo child image；真实 omp
登录态（provider `opencode-go` 的 API key）由 operator 放进持久化 `paseo_home`
卷的 `/home/paseo/.omp/` 下。模板统一使用模型
`opencode-go/muse-spark-1.3-contributor`。不要把 API key、cookie、刷新 token
或完整目录复制到仓库：

```bash
ssh -tt nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec --user paseo paseo sh'
# 在上述受控 shell 中，由 operator 按 omp 文档完成登录/写入 /home/paseo/.omp/。
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec paseo paseo provider diagnostic omp'
```

paseo workspace 自动命名会先试 `opencode-zen/*` 系列模型（返回 402/400），再回退
到 `opencode-go/minimax-m3`；paseo 日志里的这类 warning 是噪声，不是故障。

Compose sets `PASEO_HOSTNAMES` to `paseo,${ALICEDEV_HOST},localhost`: the internal
bot→paseo request uses `Host: paseo`, while the gateway's public reverse proxy uses
the DNS host. Do not remove either name or leave the public value empty. Restart/update
will make all gateway in-memory one-time tokens invalid; already issued paseo
分享 cookie 仍受签名与过期时间约束，但用户必须重新走 `/t/<token>` 获取入口。
报告链接是公开 bearer URL，不能当作私有 ACL。
Paseo launches the omp children with `ALICEDEV_INTERNAL_API=http://astrbot:6200`
and `ALICEDEV_INTERNAL_TOKEN`; never change that container-to-container URL to
`localhost`.

验证入口是 AstrBot dashboard 内置 WebChat（平台名 `webchat`）。已验证 10 个指令：
`/alicedev`（帮助）、`/需求`、`/继续`、`/需求列表`、`/收藏`、`/帮我调查`、
`/链接`、`/解读`、`/归档`、`/收藏夹`；模板 4 个：`requirement`、`investigate`、
`github-issue`、`github-pr`。

插件源码或模板更新后：

1. 先走 §2.2 的 rsync 同步；`make build`（只有 child image 或 harness 变化时必须；
   模板和 bot bind mount 变化通常不需要重建）。
2. 在 SSH 隧道中访问 AstrBot dashboard：
   `ssh -N -L 16185:127.0.0.1:6185 nekoringo2`，浏览器打开
   `http://127.0.0.1:16185`。dashboard 用户名 `astrbot`；密码 2026-09-19 已轮换
   （AstrBot 要求含大写字母、数字且 ≥8 位），只存放在服务端
   `nekoringo2:/root/alicedev-dashboard-password`（`0600`），不在其他位置记录。
3. `Extensions → Plugins → Reload Extension`，确认日志重新出现
   `alicedev initialized`；若只改了平台配置或依赖，执行
   `docker compose restart astrbot`。

## 6. 备份与恢复

先暂停写入（至少停 AstrBot，报告生成期间也停 paseo agent），再备份：

- DuckDB：AstrBot 持久化卷中的 `/AstrBot/data/plugin_data/alicedev/alicedev.duckdb`
  （宿主机不应假设插件源码 bind mount 下有数据库）。
- `alicedev_astrbot_data`：AstrBot 平台和 dashboard 配置。
- `alicedev_paseo_home`：paseo 状态、`config.json`、omp 凭据（这是秘密备份）。
- `alicedev_workspace` 与 `alicedev_reports`：agent 工作区、源报告和 `_published`。
- `alicedev_caddy_data`：ACME 证书/账户状态；不要只备份 Caddyfile。

示例（目标备份目录必须由 operator 保护，命令不打印卷内容）：

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop astrbot paseo'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_paseo_home:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 tar czf /dst/paseo_home-$(date +%Y%m%d%H%M%S).tgz -C /src .'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_astrbot_data:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 sh -c '\''tar czf /dst/duckdb-$(date +%Y%m%d%H%M%S).tgz -C /src plugin_data/alicedev/alicedev.duckdb'\''
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml start paseo astrbot'
```

恢复前停止所有消费者、先把目标卷移到隔离备份，再解包；恢复后检查
`paseo_home` 权限属于 uid/gid 1000（容器内 `paseo`），再按 §5 做 provider diagnostic
和 WebChat 小流量验证。

## 7. QQ 与 Telegram onboarding

### 7.1 QQ 官方 Bot（首选）

研究结论首选 AstrBot `qq_official`，而不是个人 QQ 协议端
([`docs/research/qq-protocol.md:97-102`](research/qq-protocol.md))。上线前必须完成：

1. QQ 开放平台创建/审核并完成上线。
2. 把 `160.191.41.242` 加入开放平台 IP allowlist。
3. 让目标群管理员添加 Bot，并开启所需的群消息范围。
4. 在 AstrBot 平台配置中选择 `qq_official`；官方 API 不需要 NapCat、OneBot、
   qsign 或额外 QQ 容器（[`docs/research/qq-protocol.md:135-152`](research/qq-protocol.md)）。

### 7.2 NapCat fallback（仅在必须使用个人 QQ 时）

若目标群不能添加官方 Bot，研究给出的 fallback 是 NapCat + AstrBot reverse
WebSocket；它需要持久化 QQ 数据，且不能保证不掉线/不封号
([`docs/research/qq-protocol.md:154-195`](research/qq-protocol.md))。仅在隔离账号上
评估，生产主 Compose 不包含它。示例片段（先固定 tag，不要直接信任 `latest`）：

```yaml
services:
  napcat:
    image: mlikiowa/napcat-docker:v4.18.28
    restart: always
    environment:
      NAPCAT_UID: "1000"
      NAPCAT_GID: "1000"
      MODE: astrbot
      TZ: Asia/Shanghai
    ports:
      - "127.0.0.1:6099:6099"
    volumes:
      - ./napcat/qq:/app/.config/QQ
      - ./napcat/config:/app/napcat/config
      - ./napcat/plugins:/app/napcat/plugins
    networks: [internal]

  # Add only the reverse-WS listener to AstrBot, bound to localhost:
  # 127.0.0.1:6199:6199; NapCat connects to ws://astrbot:6199/ws.
networks:
  internal:
    external: true
    name: alicedev
```

首次扫码只通过 SSH tunnel 打开 `127.0.0.1:6099/webui`；不要把 QQ 密码或
NapCat WebUI token 放进 Compose。风险包括 QQNT/账号风控、踢下线、设备冲突和
登录态失效；不要同时启动同一 QQ 的 NapCat、LLBot、Lagrange 或桌面 QQ。

### 7.3 Telegram

BotFather token 只写入 `deploy/.env` 的 `TELEGRAM_BOT_TOKEN`，执行
`make render-config` 后由渲染文件提供给 AstrBot。不要把 token 放到
`cmd_config.json` 的提交版本、issue、日志、截图或命令行参数中。AstrBot 当前
Telegram 配置字段和 polling 行为的事实依据见
[`docs/research/astrbot-api.md:270-290`](research/astrbot-api.md)。当前状态：
`.env` 中的 token 被 Telegram 拒绝为未授权（unauthorized），适配器保留配置但
未验证通过，见 §9。

## 8. Troubleshooting

### paseo `hello` / Host / Origin

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec paseo paseo --host 127.0.0.1:6767 hello'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec paseo sh -lc "printf \"PASEO_HOSTNAMES=%s\\n\" \"\$PASEO_HOSTNAMES\"; printf \"PASEO_PASSWORD_SET=%s\\n\" \"\${PASEO_PASSWORD:+yes}\""'
curl -si -H "Host: ${ALICEDEV_HOST}" -H "Origin: https://${ALICEDEV_HOST}" https://${ALICEDEV_HOST}/_alicedev/health
```

`403 Host not allowed` 时先看容器环境中的 `PASEO_HOSTNAMES` 是否含公共域名；不要
把 gateway/Caddy 改成绕过 paseo Host 校验。若直接 `hello` 能通而浏览器失败，继续
检查 Caddy 的 HTTP Upgrade、`Origin` 和 gateway 的 WS 上游日志。Paseo 官方配置
事实说明 `PASEO_HOSTNAMES` 是 hostnames 的部署覆盖，且密码会保护 HTTP/WS
([`paseo-alicedev/public-docs/configuration.md:167-197,238-249`](../../paseo-alicedev/public-docs/configuration.md))。

### 网关与 WebSocket

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=200 gateway caddy paseo'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec gateway python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://127.0.0.1:8080/_alicedev/health\").read().decode())"'
```

不要从浏览器向上游发送 `Authorization` 或 `Sec-WebSocket-Protocol`；这些由 gateway
注入。浏览器应该只连接 HTTPS 公共域名，刷新后仍须持有有效分享 cookie。

### 嵌入页 / self-hosted manifest

paseo web bundle 跑在 self-hosted 模式（HTTPS:443 反代后唯一可用的同源模式；
gateway commit `da1cc54`）。gateway 行为：

- `GET /_paseo/hosts.json`（需分享 cookie）返回
  `[{"id":"alicedev","label":"alicedev","basePath":"/daemons/alicedev"}]`；
  网关代理时剥离 `/daemons/alicedev` 前缀再转发给 paseo，浏览器最终拨
  `wss://alicedev.237575.xyz/daemons/alicedev/ws`。
- `/` 无分享 cookie 返回 403；`/_alicedev/health` 公开返回 200；bot 内部 API 的
  `/v1/health` 免 token 鉴权（供 astrbot healthcheck 用）。
- `/manifest.json` 无 cookie 返回 403 是 PWA manifest 的无害行为，不是部署故障；
  持有效分享 cookie 重新访问即可。

```bash
curl -si https://alicedev.237575.xyz/_alicedev/health
curl -si https://alicedev.237575.xyz/
curl -si https://alicedev.237575.xyz/manifest.json
```

### t2i 与字体

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=200 t2i astrbot'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml exec astrbot python3 -c "import urllib.request; print(urllib.request.urlopen(\"http://t2i:8999\", timeout=5).status)"'
```

AstrBot HTML 自定义渲染走 remote/network strategy，t2i sidecar 在 8999 运行；这是
研究中记录的部署方式 ([`docs/research/astrbot-api.md:343-364`](research/astrbot-api.md))。
CJK 或 emoji 缺字时，不要把宿主机字体路径误认为容器可见：把所需字体加入 t2i
child image 或模板使用可访问的字体资产/data URI，然后重启 t2i；仅改 AstrBot
`t2i_strategy=local` 不会让 HTML renderer 变成本地 Playwright。

### 配置/卷/秘密

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml config --quiet'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps --format json'
```

若 `cmd_config.rendered.json` 缺失，先确认 `deploy/.env` 非空并重跑
`make render-config`；不要把模板直接挂载成最终配置。若 paseo 重启后分享链接
失效，这是预期的内存 token 行为；重新执行 `/链接`，不要复用旧 token。

## 9. 已知限制与证据

### 9.1 当前状态

- 生产入口 `https://alicedev.237575.xyz`，`caddy`、`gateway`、`astrbot`、`t2i`、
  `paseo` 全部 healthy。
- 端口行为：`/_alicedev/health` 公开 200；`/` 无分享 cookie 时 403；
  `/manifest.json` 无 cookie 时 403（PWA manifest，无害，见 §8）。

### 9.2 已知限制

1. AstrBot WebChat ChatUI 不渲染经 `context.send_message` 发送的带外回复：
   行已写入 `platform_message_history` 且 `reply_deliveries.state=sent`（后端投递
   成功），只是 WebChat 前端不显示；真实 QQ/Telegram 平台可正常送达。
2. `.env` 中的 Telegram token 被 Telegram 拒绝为未授权：适配器保留配置但未验证
   通过。
3. QQ onboarding 未完成，仍按 §7.1 执行。

### 9.3 证据

部署证据（含 E2E transcript、截图、`share-link-check.md`、`embed-verify.md`）在
`docs/evidence/deploy-nekoringo2/`。
