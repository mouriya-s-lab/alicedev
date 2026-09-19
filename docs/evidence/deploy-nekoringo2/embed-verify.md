# Embed deep-link verification after gateway manifest fix (da1cc54)

Date: 2026-09-19 ~05:14–05:23 UTC. Production: https://alicedev.237575.xyz.
Gateway commit under test: `da1cc54 fix(gateway): serve self-hosted daemon manifest
and proxy /daemons/alicedev/* so the embed page connects`.
Browser: agent-browser, dashboard session `embed-dash-7f3a9c` (AstrBot dashboard
via `ssh -N -L 16185:127.0.0.1:6185 nekoringo2`, user `astrbot`), fresh verify
session `embed-verify-4e8b21` (closed after use; no `close --all`).
Share token: fresh one-time URL for `s_c4n74swnml` minted 05:14 UTC via
`/链接 s_c4n74swnml` in WebChat (token value redacted, never printed, never
re-opened). Session: `s_c4n74swnml`, agent
`fd91ba52-803e-4374-b2e1-904b26575cce`, workspace `wks_fb6d0ff08f05d663`,
server `srv_rAoczNBNKn6i`. No code edited. No secrets printed.

## 1. Link command — PASS

- Opened dashboard Chat (`http://127.0.0.1:16185/#/chat`), clicked into the
  `s_c4n74swnml` conversation (prior `/需求 …深色模式…` thread), sent
  `/链接 s_c4n74swnml`.
- Bot replied with a fresh one-time URL `https://alicedev.237575.xyz/t/<redacted>`
  at 02:14 local (05:14 UTC).
- Screenshot: `30-link-command.png`.

## 2. Share URL → status page — PASS

- Fresh session `embed-verify-4e8b21`, single `open <share URL>` at 05:14:49 UTC
  (first and only browser open of the token; never curled).
- Result: title `alicedev 状态`, final URL
  `https://alicedev.237575.xyz/_alicedev/?go=/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?open%3Dagent%253Afd91ba52-803e-4374-b2e1-904b26575cce%26embed%3D1`.
- Page text: `bot 状态：运行中`, `运行代数：1 · 运行时间：1113 秒`,
  `平台：telegram、webchat、webchat · 活跃会话：4 · 已关闭：0`; 10 commands,
  4 templates; link `进入会话` →
  `https://alicedev.237575.xyz/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?open=agent%3Afd91ba52-803e-4374-b2e1-904b26575cce&embed=1`.
- Screenshot: `31-status.png`.

## 3. Enter session / embed conversation — PASS (was FAIL before da1cc54)

- Clicked `进入会话`. Landed on
  `https://alicedev.237575.xyz/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?embed=1`
  (the `open=agent:…` param is consumed client-side into `?embed=1`),
  title `alicedev s_c4n74swnml`. NOT `/welcome`.
- Page shows the agent conversation: the original requirement prompt
  (深色模式 + 跟随系统设置), tool calls `Read xd://chat_reply`, `Read .`,
  `Thinking`, `Write xd://chat_reply`, summary line
  `已通过 chat_reply 回复该深色模式需求。`, `Worked for 19s`, model picker
  `Muse Spark 1.3 Contributor`, `Medium`, `Full access`, composer
  `Message the agent, tag @files, or use /commands and /skills` at the bottom.
- Eval: `sidebar=false` (no `aside`/sidebar selectors), `iframes=0`.
- `GET /_paseo/hosts.json → 200` observed in browser network log; body:
  `[{"id":"alicedev","label":"alicedev","basePath":"/daemons/alicedev"}]`.
- Console (final capture, 8 messages): only benign warnings —
  `[expo-notifications] … not yet fully supported on web` (×3),
  `Animated: useNativeDriver …` (×3), `[Session] viewed timeline
  synchronization failed {serverId: "srv_rAoczNBNKn6i", error: Subscription
  released …}` (×2). No `[HostRuntime]` warnings, no console errors.
  Browser-side WS URL not directly captured (connection predates instrumentation;
  a post-load WebSocket hook saw no new attempts), but server-side gateway log
  (item 6) proves the app dialled `/daemons/alicedev/ws` with `101` upgrades.
- Screenshot: `32-embed-session.png`.

## 4. Reload / back / forward persistence — PASS

- Reload: same URL `…/h/srv_rAoczNBNKn6i/workspace/wks_fb6d0ff08f05d663?embed=1`,
  title `alicedev s_c4n74swnml`, conversation intact (cookie persisted).
  Screenshot: `33-embed-reload.png`.
- Back → status page (`/_alicedev/?go=…`, title `alicedev 状态`); forward →
  embed conversation again (same `/h/…?embed=1`, title, thread head). Terminal
  state confirmed on the conversation before step 5.

## 5. Send message + wait for assistant reply — FAIL

- Typed `你好，请用一句话总结当前需求` in the composer (`Message agent...`)
  at 05:16:49 UTC, sent via Enter. Message appears in the thread with timestamp
  `下午2:16`; footer shows `Updating messages` spinner.
- Waited ~6 min (polls at 05:17:29, 05:17:54, 05:18:19, 05:19:09, 05:20:32,
  05:21:31, 05:22:36 UTC): no assistant reply ever appeared; spinner persisted.
- Server check: `ssh nekoringo2 'docker logs --since 8m alicedev-paseo 2>&1 |
  grep -c fd91ba52'` → `0`; also `0` over 30m/60m for both the agent id and
  `wks_fb6d0ff08f05d663`. Paseo log tail shows only routine websocket-server
  session stats. The sent message does not appear to have reached the agent.
- Screenshot: `34-embed-reply.png` (shows the user message + `Updating messages`).

## 6. Gateway log cross-check — PASS

- `ssh nekoringo2 "docker logs --since 20m alicedev-gateway 2>&1 |
  grep -E 'hosts.json|/daemons/'"` (`/t/` masked; saved verbatim to
  `35-gateway-log.txt`):
  - `GET /_paseo/hosts.json -> 200` (×4, incl. one `403` pre-open probe at 05:09)
  - `GET /daemons/alicedev/ws -> 101` (×4 at 05:15:59, 05:16:01, 05:16:28,
    05:16:38 UTC — covering embed open, reload, and reply-window reconnects)

## Verdict per step

| Step | Result |
| ---- | ------ |
| 1 link command | PASS |
| 2 status page | PASS |
| 3 embed conversation (not /welcome) | PASS |
| 4 reload + back/forward persistence | PASS |
| 5 assistant reply within 3 min | FAIL (no reply in ~6 min; paseo log count 0) |
| 6 gateway hosts.json 200 + ws 101 | PASS |

Overall: **FAIL** — the deep link now lands on the conversation (gateway fix
works), but the round-trip reply contract is not met. No code touched; left for
the owning slice.
