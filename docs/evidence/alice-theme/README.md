# Alice 黑童话主题 — 网关页面本地验收

契约：`ARCHITECTURE.md` §8「页面主题（Alice 黑童话）」与会话页、§6 `GET /v1/sessions/{id}`。分支 `feat/alice-theme`。**只在本地跑过，未上线**：生产的 Caddy → gateway 链路要等部署后再按同样的步骤验证。

## 环境

- 网关：本机 `gateway/.venv/bin/python -m alicedev_gateway`，`PUBLIC_HOST=localhost:8080`，`REPORTS_PUBLISHED_ROOT` 为临时目录，里面有一份带表格、任务列表、代码块和 mermaid 的样例报告（`r_abcdefghijklmnopqrstuvwxyz/report.md`），以及一份含非法 UTF-8 字节的 `broken.md`。
- bot：真实的 bot 代码跑在本机，用临时 DuckDB。会话由它自己的指令分发创建：`/需求 …` 得到 %1，`/帮我调查 …` 得到 %2。内部 API 开在 `:8091`。链接由 `Scheduler.issue_link` 经真实的 `GatewayClient` 向网关 `POST /internal/tokens` 签发，请求体带 `session_id`。只有 paseo 与平台发送用的是测试替身（`bot/tests/rt_support.py`）。
- paseo 上游故意没起：点「进入会话」正好触发网关连不上 paseo 的 502；真实的 paseo 工作区要等上线后在生产验证。
- 浏览器：agent-browser（Chromium，headless），桌面视口 1440×900，手机视口 390×844。

## 真实用户路径

| 步骤 | 观察 | 截图 |
|---|---|---|
| 打开 bot 为 %1 签发的链接 | 确认页自动 POST，URL 落到 `/_alicedev/?session=1&go=/h/srv1/workspace/wsl1?open%3Dagent%253Aag2`，浏览器存下并带上了 `Secure` cookie。会话页标题是会话名「需求 · K 线缓存在跨日时没有失效，开盘第一根 bar 取到前一日数据」，眉题 `ALICEDEV · %1`，下面依次是场景说明「记录群友需求并交给 AI 分析」、状态「对话中」、「进入会话」按钮和创建人/创建时间/最近活动；没有指令、场景列表或 bot 运行状态 | 03、04 |
| 打开 %2 的链接 | 同样的会话页，另有「本群当前会话」标记 | — |
| 同一链接再打开一次 | 403「这一页已经被撕掉了」，正文仍含「链接无效、已使用或已过期」 | 05、06 |
| 确认页定格：用 init 脚本把 `form.submit` 置空，只为截图 | 「正在为你翻开书页……」+「跟上爱丽丝」；手动点按钮后照常兑换并跳到会话页（按钮 fallback 有效） | 01、02 |
| 不带 cookie 的新会话访问 `/_alicedev/` | 403「这扇门还没有为你打开」，正文仍含「需要有效的 alicedev 分享授权」 | 07 |
| 带 cookie 访问 `/_alicedev/no-such-page` | 404「这条小路不通往任何地方」 | 08 |
| `/_alicedev/r/…/missing.md` | 404「这本书里没有这一章」 | 09 |
| `/_alicedev/r/…/report.md` | 羊皮纸阅读面；表格、任务列表、pygments 代码块、mermaid 都正常渲染 | 10 |
| 停掉 bot 后刷新会话页 | 仍是 200，显示「会话信息暂时取不到」，「进入会话」按钮照常可用 | 11 |
| 会话页点「进入会话」（paseo 未起） | URL 变为 `/h/srv1/workspace/wsl1?open=agent%3Aag2`，502「怀表停了一会儿」，正文仍含「paseo 上游暂时不可用」 | 12、13 |
| `/_alicedev/r/…/broken.md` | 500「这一章的墨迹糊掉了」，正文仍含「报告读取失败」 | 14 |

- 每个会话的 `agent-browser console` 和 `errors` 都是空的：没有 CSP 违规，也没有脚本错误。
- 所有页面的页脚只有 `alicedev` 字标，页面里不再出现「仙境」「茶会」。

## 卫生检查

- `cd gateway && .venv/bin/python -m pytest tests/test_gateway.py -q -p no:warnings` → `14 passed`。
- `uv run --python 3.11 --with-requirements bot/requirements.txt --with pytest python -m pytest bot/tests -q -p no:warnings` → `75 passed`。
