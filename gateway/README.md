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
docker build -f gateway/Dockerfile -t alicedev-gateway .
docker run --rm --env-file deploy/dev/gateway.env \
  -p 8080:8080 alicedev-gateway
```

`deploy/dev/gateway.env.example` 列出所有配置。`PASEO_PASSWORD` 必须非空；否则 Paseo 的 HTTP/WS 代理会失去鉴权，网关拒绝启动。

## 配置

| 环境变量 | 默认值 / 要求 | 用途 |
| --- | --- | --- |
| `GATEWAY_SECRET` | 必填 | HMAC 签名分享 cookie 的密钥 |
| `ALICEDEV_INTERNAL_TOKEN` | 必填 | bot 调用 `POST /internal/tokens` 与网关读取 bot `GET /v1/sessions/{id}` 的共享 header 密钥 |
| `PASEO_UPSTREAM` | `http://paseo:6767` | Paseo daemon 上游 |
| `PASEO_PASSWORD` | 必填且非空 | 注入 Paseo HTTP `Authorization` 及 WS 子协议 |
| `BOT_UPSTREAM` | `http://astrbot:6200` | bot 内部 API |
| `PUBLIC_HOST` | 必填 | 对外 Host；也用于 Paseo WS `Origin` |
| `REPORTS_PUBLISHED_ROOT` | 必填绝对路径 | bot 发布报告的只读目录 |

## 路由与安全边界

- `POST /internal/tokens` 只接受 `X-Alicedev-Token`，请求体 `{target, user_key, session_id, ttl_s?}`，`session_id` 必须是整数。目标只能是 `/h/<server>/workspace/<workspace>?open=agent%3A<agent>`（会话最近一个 agent）或 `/h/<server>/workspace/<workspace>`（会话还没有 agent），不带任何 Paseo 私有参数。token（连同 `session_id`）存在单进程内存表，最长 6 小时；`GET`/`HEAD /t/<token>` 只校验并 peek，不消费，返回 `Cache-Control: no-store`、`Referrer-Policy: no-referrer`、`X-Robots-Tag: noindex` 的确认页：浏览器会自动提交同一路径的 `POST` 表单，同时保留可见的手动按钮。只有 `POST /t/<token>` 执行无 await 的原子消费与过期检查；成功设置 `alicedev_s`（`iat`、`exp`、`sub`）HMAC cookie，并以 `303` 到会话页 `/_alicedev/?session=<id>&go=<target>`。重复或过期的 POST 失败。cookie 使用 `Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=2592000`。
- 免 cookie 的只有：`/t/*`、`/_alicedev/health`、`/_alicedev/r/*`（报告是公开 bearer URL）、`/_alicedev/static/*`（不含秘密；分享视图的样式表与启动脚本、报告页的 mermaid/pygments 资源都从这里加载），后两者只接受 GET/HEAD。会话页 `/_alicedev/` 与所有 Paseo 路径都需要有效 cookie。HTTP 上游始终收到网关配置的 `Authorization: Bearer $PASEO_PASSWORD`；浏览器传来的 Authorization/Cookie 不会转发。
- WebSocket 由网关分别完成浏览器和上游握手。上游收到 `Authorization`、`Host: $PUBLIC_HOST`、`Origin: https://$PUBLIC_HOST` 与 `paseo.bearer.$PASEO_PASSWORD`；浏览器不会收到该子协议或密码。
- Paseo web bundle以 `EXPO_PUBLIC_PASEO_SELFHOSTED=true` 构建（HTTPS/443 下唯一可用的同源模式）。网关提供 `GET /_paseo/hosts.json` → `[{"id":"alicedev","label":"alicedev","basePath":"/daemons/alicedev"}]`（需 cookie），并把 `/daemons/alicedev/*` 去掉前缀后转发到 Paseo（浏览器实际拨号 `wss://$PUBLIC_HOST/daemons/alicedev/ws`）。
- **分享视图**：浏览器文档请求（GET 且 `Accept` 含 `text/html`）转发上游时改发 `Accept-Encoding: identity`，拿到 `text/html` 200 后在 `<head>` 开头注入 `<script src="/_alicedev/static/paseo-boot.js"></script>`、在 `</head>` 前注入 `<link rel="stylesheet" href="/_alicedev/static/paseo-view.css">`，去掉 `Content-Length`、`Content-Encoding`、`ETag` 让 aiohttp 按新正文重算；Caddy 仍会对浏览器压缩。上游若无视 identity 仍返回 gzip/deflate，网关先解压再注入；无法解码的编码（如 br）或超过 4 MiB 的文档原样透传并记 warning。其他资源（JS/CSS/图片、HEAD、WS）逐字节流式透传。两者都不改 Paseo 源码：`static/paseo-boot.js` 在 Paseo 的 bundle 之前清掉持久化的 host 注册表，并把 host 路由改为 Paseo 自己的启动恢复，绕开首次加载落到 `/open-project` 与重复连接卡住时间线两个问题（原理见脚本头注释与 ARCHITECTURE §8）；`static/paseo-view.css` 只做隐藏。二者都是界面层处理，不是授权边界。
- **样式表选择器**取自 Paseo 的 React Native Web `testID`（渲染为 `data-testid`：`left-sidebar-resize-handle`、`sidebar-close`、`menu-button`、`workspace-new-tab-button`、`workspace-explorer-toggle` 等），绑定当前部署的 Paseo 版本。每次升级 Paseo 后都要用 agent-browser 打开一个分享链接核对：左侧栏及其导航入口不可见，会话标签、对话、输入框可见且可发消息。
- 报告只允许 `r_[a-z2-7]{26}`、单段 basename 和白名单扩展名。Markdown 使用 `html=False`、table/strikethrough/footnote/tasklists/deflist/front_matter/texmath、Pygments 及 `securityLevel: 'strict'` 的 Mermaid。响应带 `X-Robots-Tag: noindex` 与 `Referrer-Policy: no-referrer`，不提供目录列表。
- 网关自己输出的链接确认、会话页、授权/失效/找不到 403/404、paseo 不可用 502（仅浏览器文档导航；资源/XHR/WebSocket 仍是纯文本）与报告读取失败 500 统一使用 Alice 黑童话主题；主题资源仅从 `/_alicedev/static/alice/` 的精确白名单（`alice.css`、四张插画/底纹图片、`alice-icon.png`、两种 `woff2` 字体与 `OFL-IMFell.txt`）下发，按扩展名返回 MIME，白名单外静态路径保持纯文本 404。
- access log 只记录方法、脱敏路径、状态与耗时：`/t/<token>` 记为 `/t/***`，不会记录请求 query、header 或 cookie。

## 重启语义

签名 cookie 是无状态的：只要 `GATEWAY_SECRET` 不变且未过期，网关重启后仍然有效。一次性 token 只存在当前进程内存中；GET/HEAD 预览本身不会消费 token，但任何重启都会使所有尚未成功 POST 消费的 token 失效。部署 runbook 必须把这一点作为操作行为记录。

会话页只显示这个会话的信息（`%n`、会话名、状态、场景、创建人、创建时间、最近活动、是否本群当前会话），数据来自 bot 的 `GET /v1/sessions/{id}`。bot 不可达、返回非 200 或 JSON 不合法时，会话页仍返回 200，写明「会话信息暂时取不到」，「进入会话」按钮照常可用。`session` 或 `go` 缺失、不合法时返回 404 页面。

## 分享视图启动脚本（Paseo 自托管 bootstrap）

部署的 Paseo（`mouriya-s-lab/paseo@7ab7c444d`）前端对 host 路由有两个互为反面的问题，任何一次整页加载都会撞上其中一个：

- 浏览器里没有持久化的 host：`HostRuntime.runBoot` 读完本地存储就把注册表标为就绪，`bootstrapSelfHostedManifest` 的探测尚未完成，`/h/[serverId]/_layout.tsx` 找不到该 serverId，重定向到 `/welcome` → `/open-project`，深链丢失。
- 浏览器里已有持久化的 host：启动时又对 manifest 的同一连接探测一次，产生第二个 daemon socket，第一个 socket 的订阅被释放，界面停在 `Updating messages`（旧 fork 的 `6a59f442` 修过这一点）。

`static/paseo-boot.js` 在 Paseo 的 bundle 之前执行：删除 `@paseo:daemon-registry`（受管 host 全部来自 manifest，删掉不丢数据），遇到 `/h/<server>/workspace/<workspace>` 就写入 `paseo:last-workspace-route-selection` 并把地址改写为 `/`；Paseo 首页等 host 上线后按「上次活动工作区」进入该工作区，脚本在这次跳转时补回 `?open=agent:<id>`。其他 `/h/…` 地址同样改写为 `/`，由 Paseo 恢复到上次活动的工作区。

「首次加载后 `/_paseo/hosts.json` 返回空清单」不可行：`reconcileSelfHostedHostProfiles` 会删除 manifest 中不存在的受管连接，页面会退回欢迎页。

生产核对方法（升级 Paseo 后必做）：用 agent-browser 在一个干净会话里打开分享链接 → 停在工作区且时间线加载、左侧栏不可见 → 发消息并等到回复 → 刷新 → 再发消息 → 后退再前进 → 再发消息。每一步都停在工作区、时间线不卡在 `Updating messages`、能收到回复才算通过。

