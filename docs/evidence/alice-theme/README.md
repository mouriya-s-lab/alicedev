# Alice 黑童话主题 — 网关页面本地验收

契约：`ARCHITECTURE.md` §8「页面主题（Alice 黑童话）」。分支 `feat/alice-theme`。**只在本地跑过，未上线**：生产的 Caddy → gateway 链路要等部署后再按同样的步骤验证。

## 环境

- 网关：本机 `gateway/.venv/bin/python -m alicedev_gateway`，`PUBLIC_HOST=localhost:8080`，`REPORTS_PUBLISHED_ROOT` 为临时目录，里面有一份带表格、任务列表、代码块和 mermaid 的样例报告（`r_abcdefghijklmnopqrstuvwxyz/report.md`），以及一份含非法 UTF-8 字节的 `broken.md`。
- bot 状态：本地 stub 原样返回生产 bot `/v1/status` 的一次只读抓取（15 条指令、5 个场景，都只有名字；平台列表里 `webchat` 重复出现；`uptime_s=2479`）。降级状态是把 stub 停掉后得到的。
- paseo 上游故意没起：点「进入会话」正好触发网关连不上 paseo 的 502；真实的 paseo 工作区要等上线后在生产验证。
- 浏览器：agent-browser（Chromium，headless），桌面视口 1440×900，手机视口 390×844。

## 真实用户路径

| 步骤 | 观察 | 截图 |
|---|---|---|
| `POST /internal/tokens` 发一次性链接 → 浏览器打开 | 显示确认页后自动 POST，URL 落到 `/_alicedev/?go=/h/srv_1/workspace/wks_2?open%3Dagent%253Aabc-123`，浏览器存下并带上了 `Secure` cookie | 03、04 |
| 同一链接再打开一次 | 403 主题页「这一页已经被撕掉了」，正文仍含「链接无效、已使用或已过期」 | 05、06 |
| 确认页定格：用 init 脚本把 `form.submit` 置空，只为截图 | 「正在为你翻开书页……」+「跟上爱丽丝」；手动点按钮后照常兑换并跳到状态页（按钮 fallback 有效） | 01、02 |
| 不带 cookie 的新会话访问 `/_alicedev/` | 403「你还没有茶会的请柬」，正文仍含「需要有效的 alicedev 分享授权」 | 07 |
| 带 cookie 访问 `/_alicedev/no-such-page` | 404「这条小路不通往任何地方」 | 08 |
| `/_alicedev/r/…/missing.md` | 404「这本书里没有这一章」 | 09 |
| `/_alicedev/r/…/report.md` | 羊皮纸阅读面；表格、任务列表、pygments 代码块、mermaid 都正常渲染 | 10 |
| 停掉 bot stub 后刷新状态页 | 显示「♥ 降级（bot 不可用）」和 `ClientConnectorError`，各项数值显示 `—`，列表显示「暂无」 | 11 |
| 状态页点「进入会话」（paseo 未起） | URL 变为 `/h/srv_1/workspace/wks_2?open=agent%3Aabc-123`，502 主题页「怀表停了一会儿」，正文仍含「paseo 上游暂时不可用」 | 12、13 |
| `/_alicedev/r/…/broken.md` | 500 主题页「这一章的墨迹糊掉了」，正文仍含「报告读取失败」 | 14 |

- 每个会话的 `agent-browser console` 和 `errors` 都是空的：没有 CSP 违规，也没有脚本错误。
- `network requests` 里 `alice.css`、`alice-hero.webp`、`alice-emblem.webp`、`damask.webp`、`fell-sc.woff2`、`alice-icon.png` 都返回 200。

## 卫生检查

`cd gateway && .venv/bin/python -m pytest tests/test_gateway.py -q -p no:warnings` → `14 passed`。
