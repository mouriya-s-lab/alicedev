# Share-link + embed + report check (deploy-nekoringo2)

Date: 2026-09-19 ~06:03–06:05 UTC. Production: https://alicedev.237575.xyz.
Browser: agent-browser, single fresh session `slc-verify-20260919` (closed after use; no `close --all`).
Share token: one-time URL for session s_c4n74swnml (token value redacted, not printed).
No code edited (verification slice). No secrets printed.

## 1. Share URL → status page — PASS

- Fresh session, single `open <share URL>` (first and only browser open of the token).
- Result: title `alicedev 状态`, final URL
  `https://alicedev.237575.xyz/_alicedev/?go=/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?open%3Dagent%253Afd91ba52-803e-4374-b2e1-904b26575cce%26embed%3D1`
  (i.e. `/t/<token>` consumed → status page with `go=` target, per ARCHITECTURE.md §8).
- Page text: `bot 状态：运行中`, `运行代数：1 · 运行时间：414 秒`,
  `平台：telegram、webchat、webchat · 活跃会话：4 · 已关闭：0`;
  可用指令 (10): 帮我调查, 需求, 继续, 收藏, 收藏夹, 需求列表, 链接, 解读, 归档, alicedev;
  Prompt 模板 (4): github-issue, github-pr, investigate, requirement; link text `进入会话`.
- Enter-link target (snapshot `-u`, link @e4):
  `https://alicedev.237575.xyz/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?open=agent%3Afd91ba52-803e-4374-b2e1-904b26575cce&embed=1`
- Screenshot: `20-share-status.png` (35 KB).

## 2. Enter session / embed conversation + drive agent — FAIL (defect, evidence below)

- Clicked `进入会话` in the same session. Address bar first showed the embed target, then flipped to
  `https://alicedev.237575.xyz/welcome` (title `Paseo`).
- Page content (snapshot + DOM text): only `Welcome to Paseo / Connect your computer to get started /
  Direct connection / Paste pairing link / Settings / v0.8.0`. No tab bar, no conversation,
  no composer, no dark-mode messages; no sidebar either — it is the Paseo host-pairing page, not the embed.
- Direct navigation to the same embed target URL in the same session reproduced it identically
  (lands on `/welcome` with the same pairing content).
- DOM eval on the page: `url=https://alicedev.237575.xyz/welcome`, `document.cookie` names `[]`
  (cookie is HttpOnly, so empty via JS is expected), `hasLeftSidebar=false`,
  localStorage keys `paseo-drafts, @paseo:settings-migrations, @paseo:app-settings, sidebar-view,
  paseo:last-workspace-route-selection`.
- Because the conversation never loaded, the `你好，请用一句话总结当前需求` send + 3-min reply wait
  could not be performed.
- Screenshot: `21-share-embed-welcome.png` (20 KB).
- DEFECT: entering via the status-page `go` link (cookie set by the consumed one-time token) does not
  reach the shared agent workspace; paseo redirects to `/welcome` pairing instead of rendering
  `?open=agent:…&embed=1`. No code touched; left for the owning slice.

## 3. Reload / back / forward persistence — FAIL (blocked by item 2)

- Not executed: there is no embedded conversation to reload-persist. Reloading the `/welcome`
  pairing page would not test the embed contract, so it was skipped rather than recorded as evidence.

## 4. Token reuse + unauthenticated host path — PASS

- `curl -sS -o /dev/null -w '%{http_code} <- %{url_effective} (redirect: %{redirect_url})\n' <share URL>`
  (no cookie) → `403 <- https://alicedev.237575.xyz/t/<redacted> (redirect: )`. No redirect, token spent.
- `curl -sS -o /dev/null -w '%{http_code}\n' https://alicedev.237575.xyz/h/x` → `403`.

## 5. Public report rendering + headers + traversal — PASS

- Report URL `https://alicedev.237575.xyz/_alicedev/r/r_qnfekywx5qu7zuutats2kl3cvt/demo.md`
  opened in a second tab of the same session (no auth needed), title `demo.md`.
- Rendered (DOM-verified, not just visible text): 1 `h1` (`OpenAlice README 简短报告`); 1 `table`
  with 4 `tr`; 1 `pre code` block with 12 syntax-highlight `span`s (pygments classes
  `n o s2 nb p sa si …`); mermaid rendered as SVG (`.mermaid svg` count 1, total `svg` 1,
  no raw `pre.mermaid` source block); CJK present (e.g. `调查报告`, `只读公开链接`,
  `是一个面向交易场景的`, `编排器与本地工作区`); header banner `alicedev 调查报告 · 只读公开链接`.
- Screenshots: `22-report.png` (69 KB), `23-report-full.png` (87 KB, full-page).
- `curl -sSI <report URL>` → `HTTP/2 200`, `content-type: text/html; charset=utf-8`,
  `x-robots-tag: noindex`, `referrer-policy: no-referrer`, `server: Python/3.11 aiohttp/3.14.3`.
- `curl -sS -o /dev/null -w '%{http_code}\n' --path-as-is
  https://alicedev.237575.xyz/_alicedev/r/r_qnfekywx5qu7zuutats2kl3cvt/../../etc/passwd` → `404`
  (`--path-as-is` so curl does not normalize away the `../`).

## 6. Evidence commit — (this file + screenshots, pushed to main)

- Files: `20-share-status.png`, `21-share-embed-welcome.png`, `22-report.png`,
  `23-report-full.png`, `share-link-check.md` (this transcript). All screenshots < 1 MB.
