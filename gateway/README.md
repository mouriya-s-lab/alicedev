# alicedev gateway

`alicedev_gateway` 是 alicedev 的轻量 aiohttp 网关：把 bot 发出的 Paseo 工作区目标兑换成一次性分享入口，给已授权浏览器代理 Paseo HTTP/WebSocket，并以公开 bearer URL 提供已发布报告。

## 运行

Python 3.11+：

```sh
cd gateway
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/python -m alicedev_gateway
```

```sh
docker build -t alicedev-gateway gateway
docker run --rm --env-file deploy/dev/gateway.env \
  -p 8080:8080 alicedev-gateway
```

`deploy/dev/gateway.env.example` 列出所有配置。`PASEO_PASSWORD` 必须非空；否则 Paseo 的 HTTP/WS 代理会失去鉴权，网关拒绝启动。

## 配置

| 环境变量 | 默认值 / 要求 | 用途 |
| --- | --- | --- |
| `GATEWAY_SECRET` | 必填 | HMAC 签名分享 cookie 的密钥 |
| `ALICEDEV_INTERNAL_TOKEN` | 必填 | bot 调用 `POST /internal/tokens` 与读取 `/v1/status` 的共享 header 密钥 |
| `PASEO_UPSTREAM` | `http://paseo:6767` | Paseo daemon 上游 |
| `PASEO_PASSWORD` | 必填且非空 | 注入 Paseo HTTP `Authorization` 及 WS 子协议 |
| `BOT_UPSTREAM` | `http://astrbot:6200` | bot 内部 API |
| `PUBLIC_HOST` | 必填 | 对外 Host；也用于 Paseo WS `Origin` |
| `REPORTS_PUBLISHED_ROOT` | 必填绝对路径 | bot 发布报告的只读目录 |

## 路由与安全边界

- `POST /internal/tokens` 只接受 `X-Alicedev-Token`，并验证 `/h/<server>/workspace/<workspace>?open=agent%3A<agent>&embed=1` 目标。token 存在单进程内存表，最长 6 小时；`GET /t/<token>` 通过无 `await` 的 `dict.pop` 消费，设置 `alicedev_s`（`iat`、`exp`、`sub`）HMAC cookie，然后 `302` 到状态页。cookie 使用 `Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=2592000`。
- Paseo UI 除一次性入口、报告、网关静态资源和 health 外都需要有效 cookie。HTTP 上游始终收到网关配置的 `Authorization: Bearer $PASEO_PASSWORD`；浏览器传来的 Authorization/Cookie 不会转发。
- WebSocket 由网关分别完成浏览器和上游握手。上游收到 `Authorization`、`Host: $PUBLIC_HOST`、`Origin: https://$PUBLIC_HOST` 与 `paseo.bearer.$PASEO_PASSWORD`；浏览器不会收到该子协议或密码。
- 报告只允许 `r_[a-z2-7]{26}`、单段 basename 和白名单扩展名。Markdown 使用 `html=False`、table/strikethrough/footnote/tasklists/deflist/front_matter/texmath、Pygments 及 `securityLevel: 'strict'` 的 Mermaid。响应带 `X-Robots-Tag: noindex` 与 `Referrer-Policy: no-referrer`，不提供目录列表。
- access log 只记录方法、脱敏路径、状态与耗时：`/t/<token>` 记为 `/t/***`，不会记录请求 query、header 或 cookie。

## 重启语义

签名 cookie 是无状态的：只要 `GATEWAY_SECRET` 不变且未过期，网关重启后仍然有效。一次性 token 只存在当前进程内存中，任何重启都会使尚未消费的 token 失效；部署 runbook 必须把这一点作为操作行为记录。

状态页会请求 bot 的 `GET /v1/status`。bot 不可达、返回非 200 或状态 JSON 无效时，状态页仍返回 200，并明确显示「降级（bot 不可用）」而不是伪造健康状态。
