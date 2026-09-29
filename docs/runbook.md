# alicedev 运维手册（nekoringo2）

契约见 `ARCHITECTURE.md`，工具参数见 `tools/README.md`。部署（上线、构建、IaC、凭据、体检、回滚）见 `.omp/rules/deploy.md`；本文只写部署以外的宿主操作。

## 1. 连接

SSH：`ssh -i ~/.ssh/dev-dai -o IdentitiesOnly=yes root@160.191.41.242`。compose 统一用 `docker compose -f /srv/alicedev/deploy/docker-compose.yml --env-file /srv/alicedev/deploy/.env …`。

## 2. Telegram（tg-cli）

`alicedev-tg-cli` 用自己的独立授权（卷 `alicedev_tg_cli_data`）。登录：宿主上 `docker exec -i alicedev-tg-cli python /opt/alicedev/tg-qr-login.py --timeout 180` 打出 `tg://login?token=…`，开发机上用已登录的本机会话批准：`~/.local/share/uv/tools/kabi-tg-cli/bin/python tools/tg-approve-login '<url>'`。验收用的私聊名是 `RIRI OuO`（bot `@ririOuObot`）：`docker exec alicedev-tg-cli tg send "RIRI OuO" "/alicedev"`，`tg sync "RIRI OuO"` 后 `tg recent --chat "RIRI OuO" --yaml`。

## 3. QQ（snowluma）

登录态与设备身份在 `qq-gateway-data` / `qq-client-config` / `qq-client-data` 卷。首次登录是人工步骤：打开 `http://<PANEL_BIND_IP>:6081/`（netbird 内），远程桌面密码见 `docker logs alicedev-snowluma | grep 'remote desktop password'`，在桌面内扫码；扫码后 `docker restart alicedev-astrbot`，让 aiocqhttp 适配器重连。

## 4. AstrBot dashboard

只绑 `PANEL_BIND_IP:6185`（netbird 内），或走 `ssh -N -L 16185:127.0.0.1:6185` 隧道。用户名 `astrbot`。

**改 AstrBot 配置**（管理员名单、插件配置、平台）：数据卷里的 `cmd_config.json` 与 `config/alicedev_config.json` 归 AstrBot，部署不覆盖（ARCHITECTURE §11）。管理员名单在 dashboard 的插件配置页改，或直接改容器内 `/AstrBot/data/config/alicedev_config.json` 的 `admin_users`（`<platform_id>:<sender_id>`，QQ 的 platform_id 是 `qq`）；文件带 BOM，改写时按 `utf-8-sig` 读写。改完重载插件：`POST /api/v1/plugins/alicedev/reload`，头 `X-API-Key` 取宿主 `/root/alicedev-astrbot-api-key`（插件权限，热重载用它，不依赖 dashboard 密码）。dashboard 密码目前未知，记录在案的都返回 401。

新数据卷起来时 `admin_users` 是空的（种子为空列表）：先按上面设置第一位管理员并重载，再用它发一条管理员指令确认。

## 5. 已知限制

- AstrBot 主动发消息拿不到平台消息 id：引用 bot 消息时按消息首行的 `%n` 标记找会话。
- paseo 0.8.0 对 idle agent 执行 `stop` 是空操作，12h 空闲关闭只记在 `agents.status`。
- 分享视图：上游 paseo 前端的首次加载路由竞态与重复连接问题由网关注入的 `paseo-boot.js` 绕开（ARCHITECTURE §8）；它会清掉浏览器里 paseo 的 host 注册表，paseo 升级后需重新核对。
- 重启网关会使尚未兑换的一次性分享链接失效；网关镜像只由 `docker compose … build gateway` 与 `up -d --no-deps gateway` 更新。
- 所有容器同时 unhealthy、`docker exec` 报 `no space left on device`：宿主 `/tmp` tmpfs 满了，处理见 `.omp/rules/deploy.md` §3。
