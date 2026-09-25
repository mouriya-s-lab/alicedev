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

## 5. 已知限制

- AstrBot 主动发消息拿不到平台消息 id：引用 bot 消息时按消息首行的 `%n` 标记找会话。
- paseo 0.8.0 对 idle agent 执行 `stop` 是空操作，12h 空闲关闭只记在 `agents.status`。
- 分享视图：上游 paseo 前端的首次加载路由竞态与重复连接问题由网关注入的 `paseo-boot.js` 绕开（ARCHITECTURE §8）；它会清掉浏览器里 paseo 的 host 注册表，paseo 升级后需重新核对。
- 所有容器同时 unhealthy、`docker exec` 报 `no space left on device`：宿主 `/tmp` tmpfs 满了，处理见 `.omp/rules/deploy.md` §3。
