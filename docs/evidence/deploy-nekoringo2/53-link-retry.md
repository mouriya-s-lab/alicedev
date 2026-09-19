# 53 — One-time link retry and preview-safe consumption

Observed production run (UTC). This replaces the earlier retry note, which
attributed the failure to a stale administrator configuration. The final
runtime evidence identifies a different root cause: the old one-time-link
route consumed its token on `GET`, so a Telegram preview fetch could spend the
link before the operator opened it in a browser.

No token value or one-time URL is recorded here.

## Root cause and deployed contract

The failed behavior was a request-method error. The old route treated the
preview `GET` as the one-time use. The Telegram preview `GET` therefore
consumed the token before the real browser request. That explanation is the
root-cause diagnosis established by the old failure and the successful
request sequence below; the token itself is intentionally not reproduced.

The deployed route is now preview-safe: `GET` and `HEAD` return the HTML
preview without consuming the one-time link, and `POST` performs the single
consuming transition. The live run directly exercised `GET` and `POST`; the
`HEAD` behavior is the deployed route contract, not a separate request claimed
as runtime evidence in this note.

## Live request proof — PASS

The gateway request log and the Telegram/browser observations showed this
ordered sequence:

1. **21:24:40** — the gateway issued a fresh link in response to the
   `/链接` flow. The corresponding Telegram delivery was message **9604**;
   its one-time URL is redacted.
2. **21:24:42** — Telegram preview `GET` returned **200**. The link was still
   available after this preview request; it was not consumed.
3. **21:25:16** — the real browser `GET` returned **200**, the consuming
   `POST` returned **303**, and the redirected destination `GET` returned
   **200**. The browser displayed the running status page and then opened the
   actual embedded Issue #1536 workspace through `进入会话`.
4. A separate browser replay of the same one-time link was denied with
   `链接无效、已使用或已过期。`.

This sequence proves the intended boundary: preview traffic can render the
link without spending it, while one browser use consumes it exactly once.
Screenshots **[`61-link-browser-open.png`](61-link-browser-open.png)** and
**[`62-link-session-open.png`](62-link-session-open.png)** are the rendered
landing page and embedded workspace from the successful browser path.

## Reproducible observation procedure

1. Trigger `/链接` for the current conversation and record only the Telegram
   message ID; redact the returned one-time URL.
2. Observe the preview request in the gateway log. Confirm `GET` is **200**
   and that the link remains usable.
3. Open the redacted link once in a fresh browser session. Confirm the
   `GET`/**200** → `POST`/**303** → destination `GET`/**200** sequence, the
   status page, and the embedded conversation reached by `进入会话`.
4. Attempt one replay in a separate browser session. Confirm the explicit
   invalid/used/expired response.

The evidence files are the two screenshots cited above plus the gateway
request log used for the timestamp and status-code correlation. No raw token,
share URL, credential, or secret is included in this document.
