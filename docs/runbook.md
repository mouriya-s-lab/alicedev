# alicedev 部署运行手册

本文是 `nekoringo2` 上实际部署状态的记录，也是首装、升级和排障手册。
生产 Compose 只把 AstrBot dashboard 绑定到 `127.0.0.1:6185`，NapCat WebUI 绑定到
`127.0.0.1:6099`；公网入口只有 Caddy 的 80/443，Caddy 再转发到内部 gateway。
不要把 paseo、gateway、AstrBot 内部 API、OneBot reverse WS `6199` 或 t2i 端口发布到主机。
NapCat 使用 `internal` 连接 AstrBot，并额外加入 `edge` 以访问 QQ 出站服务；不启用任何
GPU 设备或 reservation。
Compose 还会启动两个短命 helper：`reports-init` 在共享卷中创建 `_published`
子目录，并把报告卷 chown 到 1000:1000（容器内 `paseo` 用户），确保 gateway
的只读 subpath mount 在首次启动时存在并 agent 可写报告；`astrbot-init` 在每次
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
`deploy/.env.example` 只有占位值。Telegram token、`NAPCAT_ONEBOT_TOKEN` 和
其他应用 secrets 通过渲染后的 AstrBot 配置/Compose 环境使用；QQ 登录密码只在
SSH tunnel 后的 NapCat WebUI 输入，从不写入 `.env`、Compose、仓库、日志或命令行。

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

提前拉取并记录生产使用的镜像。这里的 digest 是 `deploy/docker-compose.yml`
中的 amd64 pin；按 digest 引用检查镜像，因为只用 tag 的 `docker image inspect`
在 digest-only pull 后可能找不到本地 tag：

```bash
ssh nekoringo2 'docker pull caddy:2-alpine@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0'
ssh nekoringo2 'docker pull soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf'
ssh nekoringo2 'docker pull soulter/astrbot-t2i-service:latest@sha256:f7243fd37cc247f13da2b23eb394e77cae18ee7b104440ee776d55419e89f61e'
ssh nekoringo2 'docker pull mlikiowa/napcat-docker:v4.18.28@sha256:41b1a8e10953065f4796ab19c0c8760cd3175376be976c5480710d29a77357ee'
ssh nekoringo2 'for ref in caddy:2-alpine@sha256:040e9f7480b80b6d4a7e5013a21159b950a63dcbdb956e38abe2387fb28d9ec0 soulter/astrbot:latest@sha256:8e9f108c1470e6dd46bbbaad0d01e93d740dd86ee8d1cd767a10106aefd62ecf soulter/astrbot-t2i-service:latest@sha256:f7243fd37cc247f13da2b23eb394e77cae18ee7b104440ee776d55419e89f61e mlikiowa/napcat-docker:v4.18.28@sha256:41b1a8e10953065f4796ab19c0c8760cd3175376be976c5480710d29a77357ee; do docker image inspect --format="{{.Id}} {{.RepoDigests}}" "$ref"; done'
```

NapCat 的 `v4.18.28` digest 是 Docker Hub 的 `linux/amd64` manifest；Compose 同时
声明 `platform: linux/amd64`。不要把该服务改回 `latest` 或仅 tag 引用。
若镜像 registry 或 digest 发生变化，先更新 Compose pin 和本节记录，再继续；不要
为了“先跑起来”改成无 pin 的 `latest`。

### 2.2 同步源码（rsync 推送）

先在 Mac 上跑 `make harness`（`harness/dist` 产物随同步发往服务端，不在服务端
构建），记下两个本地检出的 commit，再同步：

```bash
make harness
git rev-parse HEAD
git -C /Users/mouriya/Ext/code/paseo-alicedev rev-parse HEAD
rsync -az --delete --exclude '.git/' --exclude 'node_modules/' --exclude 'harness/node_modules/' --exclude 'src/' --exclude 'deploy/.env' --exclude 'deploy/astrbot/*.rendered.json' /Users/mouriya/Ext/code/alicedev/ nekoringo2:/srv/alicedev/
ssh nekoringo2 mkdir -p /srv/alicedev/src/paseo-alicedev && rsync -az --delete --exclude '.git/' --exclude 'node_modules/' /Users/mouriya/Ext/code/paseo-alicedev/ nekoringo2:/srv/alicedev/src/paseo-alicedev/
```

说明：`deploy/.env`、`deploy/astrbot/*.rendered.json` 和 `src/` 只存在于服务端，
同步时必须排除：前两者不会被 Mac 侧覆盖；`src/` 不在 alicedev 检出里，缺少
`--exclude 'src/'` 时 `--delete` 会把 `src/paseo-alicedev` 整个删掉（只需重跑第二条
rsync 即可恢复，但要先补回排除）。QQ 身份、NapCat 配置/插件和 AstrBot 数据都在
命名 Docker volumes（`alicedev_napcat_qq`、`alicedev_napcat_config`、
`alicedev_napcat_plugins`、`alicedev_astrbot_data`）中，位于 `/srv/alicedev` 之外；
因此 rsync `--delete` 不会触碰运行时身份。禁止把这些卷改为 `./napcat/*` 或其他
工作树 bind mount。升级时先在 Mac 检出审阅 commit，记下同步时刻的 `git rev-parse HEAD`
（即审计依据），再走同一组 rsync。

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
`*.rendered.json`。前者预置 Telegram、WebChat、NapCat 的 `aiocqhttp`
reverse WS（`0.0.0.0:6199`，token 来自 `NAPCAT_ONEBOT_TOKEN`）和
`t2i_endpoint=http://t2i:8999`；后者把 paseo/gateway/报告卷和
`/AstrBot/alicedev-templates` 传给插件。模板文件使用 JSON 安全转义，不能用手工
`sed` 替换密码或 token。`astrbot-init` 在每次 `up` 时把渲染产物复制进
`astrbot_data` 卷；`NAPCAT_ONEBOT_TOKEN` 缺失时 Compose 必须拒绝启动。

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

当前服务：`caddy`、`gateway`、`astrbot`、`t2i`、`paseo` 应为 healthy；`napcat` 为
生产必需但需人工完成 QQ 登录的运行容器；另有两个一次性 helper：
`reports-init`、`astrbot-init`。其中 astrbot、napcat 和 paseo 还接入 `edge` 网络，
用于出站访问（LLM API、GitHub、QQ）；NapCat WebUI 仍只绑定 loopback。

首次启动后再初始化 workspace 卷：`make workspace-init` 把 OpenAlice 克隆到
workspace 卷的 `/workspace/openalice`（可重复执行，已存在时跳过克隆）：

```bash
ssh nekoringo2 'cd /srv/alicedev && make workspace-init'
```

`make up` 不会重新 build；每次会先用当前服务端 `deploy/.env` 运行
`make render-config`，再检查两个 rendered JSON，最后启动 Compose。源码或 harness
变化后先走 §2.2 的 rsync，再跑 `make build`；配置变化只需 `make up`。`make down`
不删除卷，避免误删会话、omp 凭据、workspace、报告和 NapCat QQ 身份。

轮换 `GATEWAY_SECRET`、`ALICEDEV_INTERNAL_TOKEN`、`PASEO_PASSWORD` 或
`NAPCAT_ONEBOT_TOKEN`（都只在服务端的 `deploy/.env`，`0600`）后，必须用
`make up` 重新渲染并启动；若绕过 Makefile 直接运行 `docker compose ... up -d`，
先手动执行 `make render-config`。轮换 `NAPCAT_ONEBOT_TOKEN` 后还要按 §7.1 在
NapCat WebUI 保存同一个新值。`astrbot-init` 会重新播种配置。轮换
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

先暂停写入（至少停 AstrBot、NapCat，报告生成期间也停 paseo agent），再备份：

- DuckDB：AstrBot 持久化卷中的 `/AstrBot/data/plugin_data/alicedev/alicedev.duckdb`
  （宿主机不应假设插件源码 bind mount 下有数据库）。
- `alicedev_astrbot_data`：AstrBot 平台和 dashboard 配置。
- `alicedev_napcat_qq`：QQNT 设备身份、登录态和 QQ 本地数据；这是敏感备份。
- `alicedev_napcat_config`：NapCat WebUI/OneBot 客户端配置（含反向 WS token）。
- `alicedev_napcat_plugins`：NapCat 插件目录。
- `alicedev_paseo_home`：paseo 状态、`config.json`、omp 凭据（这是秘密备份）。
- `alicedev_workspace` 与 `alicedev_reports`：agent 工作区、源报告和 `_published`。
- `alicedev_caddy_data`：ACME 证书/账户状态；不要只备份 Caddyfile。

示例（目标备份目录必须由 operator 保护，命令不打印卷内容）：

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml stop astrbot napcat paseo'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_napcat_qq:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 tar czf /dst/napcat_qq-$(date +%Y%m%d%H%M%S).tgz -C /src .'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_napcat_config:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 tar czf /dst/napcat_config-$(date +%Y%m%d%H%M%S).tgz -C /src .'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_napcat_plugins:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 tar czf /dst/napcat_plugins-$(date +%Y%m%d%H%M%S).tgz -C /src .'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_paseo_home:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 tar czf /dst/paseo_home-$(date +%Y%m%d%H%M%S).tgz -C /src .'
ssh nekoringo2 'mkdir -p /srv/alicedev/backups && docker run --rm -v alicedev_astrbot_data:/src:ro -v /srv/alicedev/backups:/dst alpine:3.22 sh -c '\''tar czf /dst/duckdb-$(date +%Y%m%d%H%M%S).tgz -C /src plugin_data/alicedev/alicedev.duckdb'\''
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml start paseo napcat astrbot'
```

恢复前停止所有消费者、先把目标卷移到隔离备份，再解包；恢复后检查
`paseo_home` 权限属于 uid/gid 1000（容器内 `paseo`），再按 §5 做 provider diagnostic
和 WebChat 小流量验证。恢复 `alicedev_napcat_qq` 后必须确认 QQ 设备身份仍为同一账号；
不要为了“修复”登录态删除该卷。

## 7. QQ 与 Telegram onboarding

### 7.1 QQ NapCat（生产必需）

本项目的操作员约束是使用个人 QQ 账号并接入 OneBot v11；因此生产路径固定为
NapCat（NTQQ/Linux QQ）+ AstrBot `aiocqhttp` reverse WebSocket。这里的 NapCat
不是可选 fallback；Compose、AstrBot 配置和备份都按它存在来设计。候选比较和风险
依据见 [`docs/research/qq-protocol.md`](research/qq-protocol.md)，但研究中的其他
候选不是本项目的生产 onboarding。当前文档**不声称 QQ 已登录或上线**；只有完成
本节并观察到 §8 的连接证据后，才能记录为已完成。

#### 首次启动与 token

1. 在受控终端生成一次随机 `NAPCAT_ONEBOT_TOKEN`（例如
   `openssl rand -hex 32`），把结果写入服务端 `deploy/.env`，并保持该文件
   `0600`。该 token 只用于 AstrBot ↔ NapCat 的 reverse WS；不要把它贴到聊天、
   issue、日志或截图。`cmd_config.json` 的占位符由现有 `make render-config`
   渲染，Compose 在 token 缺失时拒绝启动。
2. 按 §2 完成镜像、渲染和启动。NapCat 的 QQ 身份、WebUI 配置和插件写入
   `alicedev_napcat_qq`、`alicedev_napcat_config`、`alicedev_napcat_plugins`
   命名卷；不要改成工作树 bind mount。
3. 需要 WebUI 访问 token 时，只在服务器受控 shell 中查看
   `docker compose ... logs napcat` 的相关行，然后通过下面的隧道在浏览器输入；
   不要把它写入 `.env` 或仓库。

#### 选择 WebUI 密码登录

1. 从运维机建立隧道（NapCat 只绑定服务器 loopback）：

   ```bash
   ssh -N -L 16099:127.0.0.1:6099 nekoringo2
   ```

2. 在本机浏览器打开 `http://127.0.0.1:16099/webui`。如 WebUI 要求 NapCat
   WebUI access token，输入第 3 步从受控日志取得的值。
3. 在 WebUI 选择**密码登录**，只在此隧道后的页面输入 QQ 账号和 QQ 密码。
   QQ 密码永远不进入 Compose、`.env`、命令行、浏览器自动化脚本、日志、截图或
   备份说明；本项目不提供密码环境变量，也不声称密码登录已完成。
4. 如果 QQ 要求验证码、滑块、短信、人脸或“新设备登录”确认，停止自动重试，
   按 QQ 手机端/安全中心的提示人工完成验证，再回到 WebUI 继续一次登录。验证
   失败时不要删除 `alicedev_napcat_qq`、反复扫描二维码、频繁换 IP 或切换协议端；
   这些动作会丢失设备身份或放大风控。
5. 登录成功后，在 NapCat WebUI 保存 OneBot v11 **反向 WebSocket 客户端**：
   - URL：`ws://astrbot:6199/ws`（NapCat 主动连接；不能填 `127.0.0.1`）。
   - token：与 `deploy/.env` 的 `NAPCAT_ONEBOT_TOKEN` 完全相同。
   - 启用客户端并保存；字段名称随 NapCat WebUI 版本可能略有不同，但角色、
     URL、token 和 `/ws` 路径不变。
   AstrBot 已由 `cmd_config.json` 预置为 `0.0.0.0:6199` 的 `aiocqhttp` server；
   宿主机不发布 6199。

#### 后续快速登录、日志和重连

- 首次成功后，QQNT 登录态会留在 `alicedev_napcat_qq`；之后重启/升级应使用
  持久化快速登录，不在自动化中再次提交 QQ 密码。`alicedev_napcat_config`
  保存 reverse WS 目标和 token；变更 token 时同时更新 `.env`、重渲染 AstrBot
  配置并在 NapCat WebUI 保存新 token。
- 观察连接必须同时满足：NapCat 日志显示 QQ online/登录成功；AstrBot 日志出现
  `aiocqhttp(OneBot v11) adapter connected.`；没有持续的 reverse WS close/heartbeat
  timeout；白名单测试群收到一条低频测试消息。容器 `running` 单独不等于 QQ 在线。
- 排障先看 `docker compose ... logs --tail=200 napcat astrbot`。`KickedOffline`、
  `登录已失效`、`账号异常`、验证码/新设备提示表示 QQ 会话问题；`aiocqhttp
  adapter has been closed` 或 heartbeat timeout 表示 OneBot 链路断开，不能直接
  判定封号。先检查命名卷未被替换、URL/token 是否一致和 AstrBot 6199 是否仅在
  Compose 网络可达，再人工决定是否重启。
- 进程崩溃可由 `restart: unless-stopped` 恢复；QQ 掉线或新设备验证不要做毫秒级
  无限重启。采用人工确认和退避，连续失败就停下并保留完整脱敏日志；不要为重登
  删除卷、启用 GPU、使用公共 sign server 或并行试验多个协议端。
- **同一 QQ 只能同时运行一个客户端/协议端**：NapCat、桌面 QQ、LLBot、
  Lagrange、go-cqhttp 等不能并行。尤其不要为了比较稳定性让同一 QQ 同时登录，
  这会制造真实的互踢和设备冲突。
- QQ 身份和 NapCat 配置备份按 §6 执行；备份属于敏感凭据材料，恢复时必须保持
  同一命名卷和账号身份，不要把卷导出到源码同步树。

### 7.2 Telegram

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

### NapCat / QQ reverse WebSocket

```bash
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml logs --tail=200 napcat astrbot'
ssh nekoringo2 'cd /srv/alicedev && docker compose --env-file deploy/.env -f deploy/docker-compose.yml ps napcat astrbot'
```

正常链路应同时有：NapCat 的 QQ online/登录成功状态、AstrBot 的
`aiocqhttp(OneBot v11) adapter connected.`，以及 NapCat 持续连接
`ws://astrbot:6199/ws`。只看到 NapCat 容器 `running` 不代表 QQ 已在线。
`KickedOffline`、`登录已失效`、`账号异常`、滑块/新设备提示属于 QQ 会话验证；
`aiocqhttp adapter has been closed`、reverse WS close 或 heartbeat timeout 属于
OneBot 链路。先从同一 SSH tunnel 打开 WebUI 检查在线状态和客户端 URL/token，
再按 §7.1 的人工验证与退避处理；不要删除 `alicedev_napcat_qq`，也不要并行
启动同一 QQ 的其他客户端。服务器没有 6199 的 host listener，外部只应能看到
loopback WebUI 6099。

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
  `paseo` 的应用 healthcheck 目标为 healthy；NapCat 容器已纳入 Compose，但 QQ
  登录/OneBot 连接仍未完成，不能据此声称 QQ 已上线。
- 端口行为：`/_alicedev/health` 公开 200；`/` 无分享 cookie 时 403；
  `/manifest.json` 无 cookie 时 403（PWA manifest，无害，见 §8）。

### 9.2 已知限制

1. AstrBot WebChat ChatUI 不渲染经 `context.send_message` 发送的带外回复：
   行已写入 `platform_message_history` 且 `reply_deliveries.state=sent`（后端投递
   已证），只是 WebChat 前端不显示；QQ/Telegram 真实平台的送达尚未验证（见 2、3）。
2. `.env` 中的 Telegram token 被 Telegram 拒绝为未授权：适配器保留配置但未验证
   通过。
3. QQ onboarding 未完成，仍按 §7.1 执行。
4. 未在生产逐一执行的指令：`/解读`、`/归档`、`/收藏夹`（仅确认已注册，单测覆盖）。
5. 12 小时空闲 sweeper 未在生产观察到真实关闭事件；At 多人 fan-out 与 Reply 引用
   只覆盖 WebChat 路径。
6. 嵌入页刷新/前进后退修复（paseo-alicedev `6a59f442`）只在单一干净浏览器会话验证；
   多标签页、断网重连、长时间会话未验证。分享 cookie 只观察到单次消费与重放 403，
   未验证过期与跨浏览器行为。
7. paseo-alicedev 的 pre-commit 因本机 mise 未信任仓库 `.mise.toml` 未执行（仅跑了
   `host-runtime.test.ts` 72/72）。
8. DNS 记录未纳入 IaC（§4）。

### 9.3 证据

部署证据（含 E2E transcript、截图、`share-link-check.md`、`embed-verify.md`、
`embed-reply-sync.md`）在 `docs/evidence/deploy-nekoringo2/`。
