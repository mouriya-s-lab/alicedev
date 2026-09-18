# OMP extension/API facts for alicedev

Investigation target: the checked-out Oh My Pi tree at `/Users/mouriya/Ext/code/oh-my-pi`, the reference configuration repo at `/Users/mouriya/Ext/code/omp-config`, and the installed local `pi`. This is read-only source investigation; no repository files were changed and no tests/builds/formatters were run.

## Executive decision

**The supported outside-process control plane is a long-lived stdio protocol process, not a socket/HTTP attachment.** Start one OMP child with `--mode rpc` or `acp`, keep stdin open, and send JSONL/JSON-RPC messages. RPC can inject `prompt`, `steer`, and `follow_up` into the same live `AgentSession`; ACP additionally exposes machine-readable `session/list`, `session/resume`, and `session/prompt`. A separate `omp --resume <id> -p ...` opens the persisted JSONL in a new process and performs one headless turn; it is not an attach operation to an already-running process. The source explicitly describes RPC as stdin JSON commands/stdout events and ACP as JSON-RPC over stdio ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:793-801,1620-1650`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:793-801,1620-1650); [`docs/rpc.md:1-30`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/rpc.md:1-30); [`packages/coding-agent/src/modes/acp/acp-mode.ts:39-62`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-mode.ts:39-62)).

For alicedev, load one extension into that child. Use OMP's `session_stop` continuation hook for the “chat reply was not consumed” reminder, rather than returning from `turn_end`: `turn_end` is notification-only, while `session_stop` can return `{ continue: true, additionalContext }`, which OMP stores as a hidden synthetic continuation message ([`packages/coding-agent/src/extensibility/extensions/types.ts:1271-1277`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1271-1277); [`packages/coding-agent/src/extensibility/shared-events.ts:393-403`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/shared-events.ts:393-403); [`packages/coding-agent/src/session/agent-session.ts:4094-4147`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:4094-4147)).

There is one important ingress gap: OMP's native `InputEvent` contains only `text`, optional `images`, and `source`; it has **no opaque message metadata/message_id field** ([`packages/coding-agent/src/extensibility/extensions/types.ts:908-914`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:908-914)). Therefore, to preserve an AstrBot/QQ/Telegram `message_id` while keeping it out of model context, the bridge must use an extension-owned ingress command/sideband (recommended below), or OMP itself would need a transport patch. Do not assume an arbitrary RPC JSON field will be handed to an extension.

## 1. Extension locations, loading, and per-session binding

### Native and configured locations

OMP auto-discovers native extension modules from:

- `<cwd>/.omp/extensions/`
- the active agent directory's `extensions/`, default `~/.omp/agent/extensions/`
- legacy `extensions` lists in `<cwd>/.omp/settings.json` and the active agent directory's `settings.json`
- enabled installed-plugin manifests (`omp.extensions` or legacy `pi.extensions`)
- explicit `--extension/-e` and `--hook` paths, plus the merged `extensions:` config array.

These locations and precedence are documented at [`docs/extension-loading.md:26-91`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extension-loading.md:26-91). Native/configured directory scans load direct `.ts`/`.js` files and one-level entry directories; package manifests can name broader `.ts/.js/.mjs/.cjs` entries ([`docs/extension-loading.md:45-57`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extension-loading.md:45-57); [`packages/coding-agent/src/extensibility/extensions/loader.ts:511-535`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/loader.ts:511-535)). `--no-extensions` disables ambient discovery; explicit paths remain separately controllable ([`docs/extension-loading.md:95-103`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extension-loading.md:95-103)).

The reference `omp-config` repo matches this shape: it says local extensions are `agent/extensions/*.ts`, default-export a function that registers tools/commands or hooks, and are loaded per session ([`/Users/mouriya/Ext/code/omp-config/AGENTS.md:41-48,92-119`](\/Users\/mouriya\/Ext\/code\/omp-config\/AGENTS.md:41-48,92-119)). Its current snapshot config has `extensions: [~/.claude]` ([`/Users/mouriya/Ext/code/omp-config/agent/config.yml:1-3`](\/Users\/mouriya\/Ext\/code\/omp-config\/agent\/config.yml:1-3)) and legacy `settings.json` repeats that path ([`/Users/mouriya/Ext/code/omp-config/agent/settings.json:1-3`](\/Users\/mouriya\/Ext\/code\/omp-config\/agent\/settings.json:1-3)). Those paths are user-specific reference data, not a recommendation for alicedev.

### Factory and binding

The module contract is a default factory:

```ts
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default function alicedev(pi: ExtensionAPI): void | Promise<void> {
  // register tools/commands and event handlers
}
```

The exact factory type is `ExtensionFactory = (pi: ExtensionAPI) => void | Promise<void>` ([`packages/coding-agent/src/extensibility/extensions/types.ts:1622-1623`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1622-1623)). OMP imports all candidate modules, then binds their factories sequentially into an extension runtime ([`packages/coding-agent/src/extensibility/extensions/loader.ts:451-485`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/loader.ts:451-485)). Registration methods are valid while the factory runs; runtime actions are only initialized after `ExtensionRunner.initialize` ([`docs/extensions.md:38-65`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:38-65)). Thus `pi.registerTool`, `pi.registerCommand`, and `pi.on` belong in the factory; do not call `pi.sendUserMessage` from module-load time.

The concrete loader stores handlers and tool definitions on the extension and delegates runtime actions to the active session runtime ([`packages/coding-agent/src/extensibility/extensions/loader.ts:149-187,259-275`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/loader.ts:149-187,259-275)). This means any closure map is process/session runtime state, not durable storage. Rebuild it at `session_start`, and clean it at `session_shutdown`.

## 2. Exact OMP API surface relevant to alicedev

### Context and session identity

`ExtensionContext` exposes `mode: "tui" | "rpc" | "json" | "print"`, `cwd`, read-only `sessionManager`, current model, idle/pending state, abort/shutdown, and the effective system prompt ([`packages/coding-agent/src/extensibility/extensions/types.ts:452-499`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:452-499)). `ReadonlySessionManager` exposes `getSessionDir`, `getSessionId`, `getSessionFile`, `getArtifactsDir`, `getBranch`, `getEntries`, `getHeader`, and related read-only accessors ([`packages/coding-agent/src/session/session-manager.ts:376-400`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:376-400)). The concrete getters are [`getSessionId(): string`, `getSessionFile(): string | undefined`, and `getArtifactsDir()`]([`packages/coding-agent/src/session/session-manager.ts:2084-2114`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:2084-2114)).

`session_start` has only `{ type: "session_start" }`; obtain the ID/file from `ctx.sessionManager` ([`packages/coding-agent/src/extensibility/shared-events.ts:27-30`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/shared-events.ts:27-30)). `session_stop` is the exception: its event includes `session_id`, optional `session_file`, `messages`, `turn_id`, last assistant message, and an abort signal ([`packages/coding-agent/src/extensibility/shared-events.ts:96-107`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/shared-events.ts:96-107)).

### Registration and handler signatures

`ExtensionAPI.on` has typed overloads for lifecycle events. The relevant overloads are:

```ts
pi.on("session_start", handler: ExtensionHandler<SessionStartEvent>): void;
pi.on("before_agent_start", handler: ExtensionHandler<BeforeAgentStartEvent, BeforeAgentStartEventResult>): void;
pi.on("input", handler: ExtensionHandler<InputEvent, InputEventResult>): void;
pi.on("turn_start", handler: ExtensionHandler<TurnStartEvent>): void;
pi.on("turn_end", handler: ExtensionHandler<TurnEndEvent>): void;
pi.on("session_stop", handler: ExtensionHandler<SessionStopEvent, SessionStopEventResult>): void;
pi.on("session_shutdown", handler: ExtensionHandler<SessionShutdownEvent>): void;
```

The real overload block is [`packages/coding-agent/src/extensibility/extensions/types.ts:1244-1300`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1244-1300), and `ExtensionHandler` is `(event, ctx) => Promise<R | void> | R | void` ([`packages/coding-agent/src/extensibility/extensions/types.ts:1203-1208`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1203-1208)). There is no `session_end` or `before_yield` event in this public overload list. Use `session_shutdown` for teardown and `session_stop` for the pre-settle continuation point.

`registerTool` takes `ToolDefinition<TParams, TDetails>`; the required fields are `name`, `label`, `description`, `parameters`, and `execute(toolCallId, params, signal, onUpdate, ctx): Promise<AgentToolResult<TDetails>>` ([`packages/coding-agent/src/extensibility/extensions/types.ts:620-676`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:620-676); [`packages/coding-agent/src/extensibility/extensions/types.ts:1302-1307`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1302-1307)). OMP's parameter field accepts the injected Zod-compatible builder (`pi.zod`), native `pi.arktype`, or legacy `pi.typebox` ([`docs/extensions.md:178-184`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:178-184); [`docs/skills/authoring-extensions.md:163-194`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/skills\/authoring-extensions.md:163-194)).

`registerCommand` is the right API for `/需求`-style slash commands. The reference full example shows `pi.registerCommand("greet", { description, handler: async (args, ctx) => ... })` ([`docs/skills/authoring-extensions.md:24-73`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/skills\/authoring-extensions.md:24-73)). An extension command is handled before normal agent prompt processing, including in RPC mode ([`packages/coding-agent/src/session/agent-session.ts:6126-6147`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:6126-6147); [`packages/coding-agent/src/modes/rpc/rpc-mode.ts:1130-1161`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:1130-1161)).

### Hooks and message delivery

- `before_agent_start` runs after prompt submission and before the agent loop. It can return `{ message: CustomMessagePayload }` and/or a replacement `systemPrompt` ([`packages/coding-agent/src/extensibility/extensions/types.ts:763-769,1149-1153`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:763-769,1149-1153)). Use this for turn-specific system/context additions, not as the inbound metadata carrier.
- `input` sees raw input before skill/template expansion and can return `handled`, `transform`, or `continue`; its source is `interactive | rpc | extension` but it has no message ID field ([`docs/extensions.md:909-958`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:909-958); [`packages/coding-agent/src/extensibility/extensions/types.ts:908-914,1125-1133`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:908-914,1125-1133)).
- `turn_start` is `{ turnIndex, timestamp }`; `turn_end` is `{ turnIndex, message, toolResults }` ([`packages/coding-agent/src/extensibility/shared-events.ts:204-217`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/shared-events.ts:204-217)). OMP forwards the event and ignores a `turn_end` return value ([`packages/coding-agent/src/session/agent-session.ts:4170-4178`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:4170-4178)).
- `session_stop` is awaited after terminal turn maintenance, and a nonempty continuation context with `continue: true` or `decision: "block"` schedules another turn; OMP caps the continuation chain at eight ([`docs/extensions.md:293-303`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:293-303); [`packages/coding-agent/src/extensibility/extensions/runner.ts:1397-1405`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/runner.ts:1397-1405); [`packages/coding-agent/src/session/agent-session.ts:4120-4133`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:4120-4133)).
- `session_shutdown` fires on process/session-runtime teardown; use it to stop watchers and clear process resources ([`packages/coding-agent/src/extensibility/shared-events.ts:91-94`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/shared-events.ts:91-94)).

Runtime actions:

```ts
pi.sendMessage(
  { customType, content, display, details, attribution },
  { triggerTurn?: boolean, deliverAs?: "steer" | "followUp" | "nextTurn" | "aside" },
): void;

pi.sendUserMessage(
  string | (TextContent | ImageContent)[],
  { deliverAs?: "steer" | "followUp" | "aside" },
): void;

pi.appendEntry<T = unknown>(customType: string, data?: T): void;
pi.exec(command: string, args: string[], options?: ExecOptions): Promise<ExecResult>;
```

These are the exact OMP declarations ([`packages/coding-agent/src/extensibility/extensions/types.ts:1427-1455`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1427-1455)). Delivery semantics are documented as: `steer` interrupts/queues into a live run, `followUp` waits until the current run is done, `nextTurn` stores hidden context for the next user prompt, and `aside` injects at the next step boundary ([`docs/extensions.md:186-198`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:186-198)). `sendUserMessage` follows prompt flow and is the actual user-visible/user-attributed message path; `sendMessage` creates a custom message. `appendEntry` is durable extension state and is **not** sent to the LLM ([`packages/coding-agent/src/session/session-manager.ts:2466-2469`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:2466-2469); [`docs/extensions.md:1416-1473`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extensions.md:1416-1473)).

A custom message's `content` participates in LLM context, while `details` is extension-specific metadata not sent to the LLM when persisted ([`packages/coding-agent/src/session/session-manager.ts:2481-2514`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:2481-2514)). This is the native way to persist an internal correlation record without putting its details in the model prompt.

## 3. Outside-process start, resume, list, and injection

### Exact OMP startup commands

Use `--thinking` for the reasoning/effort selector; OMP has no native `--effort` flag. The parser accepts `--mode`, `--provider`, `--model`, `--system-prompt`, `--append-system-prompt`, `--session-dir`, and `--thinking` ([`packages/coding-agent/src/cli/flag-tables.ts:123-179,195-205`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/cli\/flag-tables.ts:123-179,195-205)). The supported thinking levels are documented as `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `auto` ([`docs/cli-reference.md:98-105`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/cli-reference.md:98-105)).

**Long-lived RPC child (recommended for a running paseo session):**

```bash
omp --mode rpc \
  --system-prompt "$PASEO_PROMPT" \
  --provider "$PROVIDER" \
  --model "$MODEL" \
  --thinking "$EFFORT"
```

The process owns stdin until EOF, emits a `ready` frame, and then accepts NDJSON commands ([`docs/rpc.md:16-30`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/rpc.md:16-30); [`packages/coding-agent/src/modes/rpc/rpc-mode.ts:802-837`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:802-837)). Keep the child stdin pipe open; closing it causes command drain, session disposal, and process exit ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:1625-1650`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:1625-1650)). Omit `--no-session` when the paseo session must be persisted.

**One-shot resumed injection (new process, not live attach):**

```bash
printf '%s\n' "$FOLLOW_UP" | omp \
  --resume "$SESSION_ID" \
  --system-prompt "$PASEO_PROMPT" \
  --provider "$PROVIDER" \
  --model "$MODEL" \
  --thinking "$EFFORT" \
  -p
```

The CLI resolves `--resume <id>` as a session ID prefix/path and opens the JSONL with `SessionManager.open` ([`packages/coding-agent/src/main.ts:953-1026`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/main.ts:953-1026)). Non-TTY stdin becomes the initial prompt, and print mode processes it then exits ([`packages/coding-agent/src/main.ts:202-218,1492-1496`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/main.ts:202-218,1492-1496); [`packages/coding-agent/src/modes/print-mode.ts:89-93,169-181,240-247`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/print-mode.ts:89-93,169-181,240-247)). This is suitable for a serialized queue worker, but it does not attach to an already-running OMP process; that conclusion follows from the resume path opening a new manager and the live RPC runner owning its own stdin ([`packages/coding-agent/src/main.ts:983-1025`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/main.ts:983-1025); [`packages/coding-agent/src/modes/rpc/rpc-mode.ts:793-801`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:793-801)) [INFERENCE]. Avoid concurrent writers to the same JSONL unless a separate queue/lock policy is established.

**Interactive picker/list behavior:** `omp --resume` without a value opens the picker; the documented CLI does not expose a machine-readable `omp sessions --json` command ([`docs/cli-reference.md:69-79`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/cli-reference.md:69-79); [`packages/coding-agent/src/main.ts:1030-1033`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/main.ts:1030-1033)). Programmatic filesystem listing is available through `listAllSessions(...)`, scanning `*/*.jsonl`, and resume matching accepts UUID/filename prefixes ([`packages/coding-agent/src/session/session-listing.ts:611-639,650-657,711-730`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-listing.ts:611-639,650-657,711-730)). For a transport-level machine list, use ACP `session/list` below.

### RPC injection into the same running process

RPC commands are exact JSON objects:

```json
{"id":"req-1","type":"prompt","message":"/需求 implement the feature"}
{"id":"req-2","type":"steer","message":"Stop and report the blocker"}
{"id":"req-3","type":"follow_up","message":"After the current turn, summarize"}
{"id":"req-4","type":"get_state"}
```

`RpcCommand` defines `prompt`, `steer`, `follow_up`, `abort`, `abort_and_prompt`, model selection, thinking selection, and state/session commands ([`packages/coding-agent/src/modes/rpc/rpc-types.ts:28-93`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-types.ts:28-93)). The handler maps `steer` to `session.steer(...)` and `follow_up` to `session.followUp(...)` ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:1181-1202`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:1181-1202)); OMP's underlying signatures are `async steer(text, images?)` and `async followUp(text, images?, options?)` ([`packages/coding-agent/src/session/agent-session.ts:6779-6813`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:6779-6813)).

`prompt` is acknowledged when accepted, not when the model turn finishes; correlate later `agent_end`, `turn_end`, `message_end`, and related event frames ([`docs/rpc.md:92-105,209-231`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/rpc.md:92-105,209-231)). `get_state` returns `sessionFile`, `sessionId`, model, thinking level, streaming state, queue count, and message count ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:1216-1244`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:1216-1244)).

If the process is already running and the bridge needs to change model/effort, send:

```json
{"id":"model-1","type":"set_model","provider":"$PROVIDER","modelId":"$MODEL"}
{"id":"thinking-1","type":"set_thinking_level","level":"high"}
```

The command union exposes those fields ([`packages/coding-agent/src/modes/rpc/rpc-types.ts:51-58`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-types.ts:51-58)).

### ACP list/resume/injection

ACP is OMP's JSON-RPC-over-stdio server. Start it with either:

```bash
omp acp \
  --system-prompt "$PASEO_PROMPT" \
  --provider "$PROVIDER" \
  --model "$MODEL" \
  --thinking "$EFFORT"
# equivalent launch mode:
omp --mode acp ...
```

The CLI documents ACP as an Agent Client Protocol server over stdio ([`docs/cli-reference.md:193-213`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/cli-reference.md:193-213)); the mode converts process stdin/stdout to an NDJSON JSON-RPC stream ([`packages/coding-agent/src/modes/acp/acp-mode.ts:39-62`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-mode.ts:39-62)).

The client methods and wire methods are:

```ts
connection.newSession({ cwd, mcpServers, additionalDirectories? }); // session/new
connection.listSessions({ cwd?, cursor?, additionalDirectories? }); // session/list
connection.resumeSession({ sessionId, cwd, mcpServers, additionalDirectories? }); // session/resume
connection.prompt({ sessionId, prompt: [{ type: "text", text }] }); // session/prompt
connection.cancel({ sessionId }); // session/cancel notification
```

These exact methods are in [`packages/utils/src/acp/connection.ts:193-232`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/utils\/src\/acp\/connection.ts:193-232). Request/response payloads are defined at [`packages/utils/src/acp/protocol.ts:234-287,345-362`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/utils\/src\/acp\/protocol.ts:234-287,345-362). OMP advertises `loadSession`, list/fork/resume/close capabilities and embedded/image prompt support ([`packages/coding-agent/src/modes/acp/acp-agent.ts:631-675`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-agent.ts:631-675)).

ACP `session/list` flushes currently loaded sessions and lists persisted session metadata; `session/resume` returns an existing in-memory record when present or opens its stored JSONL ([`packages/coding-agent/src/modes/acp/acp-agent.ts:714-740,1247-1275`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-agent.ts:714-740,1247-1275)). A `session/prompt` received while another prompt is live implicitly initiates cancellation and queues the new prompt after cleanup; explicit `session/cancel` is also supported ([`packages/coding-agent/src/modes/acp/acp-agent.ts:820-892,1072-1107`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-agent.ts:820-892,1072-1107)). For chat bridge behavior where “new inbound text should not abort the current turn,” prefer RPC `follow_up` or explicitly serialize ACP prompts; ACP's bare `session/prompt` is not the equivalent of a non-interrupting follow-up.

### Hub, Unix socket, and HTTP findings

Agent Hub is the in-process TUI/agent peer surface. Its documented `hub send` steers/follows up with a **normal subagent** in the current OMP session; it is not a documented control endpoint for an unrelated OMP process ([`docs/agent-hub.md:1-5,59-93`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/agent-hub.md:1-5,59-93)).

A scoped source search of `packages/coding-agent/src` for `unix|socket|http|listen|WebSocketServer|Bun.serve` found listeners for auxiliary blob broker, debugger, LSP, tiny-model workers, and eval/tool bridges, but no session-control listener; the RPC and ACP runners explicitly use stdio ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:793-801`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:793-801); [`packages/coding-agent/src/modes/acp/acp-mode.ts:39-62`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-mode.ts:39-62)). Treat a Unix socket/HTTP attach API as **not present in this checkout** [OBSERVED SEARCH; absence is not a promise about future versions]. The `collab` feature is a separate encrypted WebSocket relay/browser collaboration surface, not a local process attachment API ([`docs/collab.md:20-75,98-123`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/collab.md:20-75,98-123)).

## 4. Session disk layout and IDs

OMP sessions are append-only JSONL journals with one header plus entries forming a tree by `(id,parentId)` ([`packages/coding-agent/src/session/session-manager.ts:453-475`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:453-475)). The default root is `~/.omp/agent/sessions` ([`packages/utils/src/dirs.ts:567-570,871-873`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/utils\/src\/dirs.ts:567-570,871-873)). The per-project directory is derived from the cwd, e.g. home-relative paths use a `-`-prefixed encoded directory and non-home paths use a legacy absolute encoding; `computeDefaultSessionDir` joins that name under the sessions root ([`packages/coding-agent/src/session/session-paths.ts:35-87,181-196`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-paths.ts:35-87,181-196)).

A new session ID is `Bun.randomUUIDv7()`. The persisted filename is `${fileSafeTimestamp(timestamp)}_${sessionId}.jsonl`, where the timestamp replaces `:` and `.` with `-`; the header stores the same ID, timestamp, cwd, and version ([`packages/coding-agent/src/session/session-manager.ts:98-107,1109-1164`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:98-107,1109-1164)). Session persistence is lazy: the path can be allocated before the JSONL exists; materialization happens after an assistant message or explicit `ensureOnDisk()` ([`packages/coding-agent/src/session/session-manager.ts:2096-2109`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:2096-2109)). A bridge should not advertise a resumable session until `isSessionOnDisk()` is true.

For per-session pending chat state, use an in-memory map keyed by `ctx.sessionManager.getSessionId()` and append durable custom entries such as `alicedev.chat.pending` and `alicedev.chat.consumed`. `pi.appendEntry` is not sent to the model; restore by scanning `ctx.sessionManager.getBranch()`/`getEntries()` during `session_start` ([`packages/coding-agent/src/session/session-manager.ts:376-400`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:376-400); [`packages/coding-agent/src/extensibility/extensions/types.ts:1451-1452`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1451-1452)).

### Local `pi` comparison

Read-only local command observations:

```text
which pi             -> /opt/homebrew/bin/pi
pi --version         -> 0.85.1
realpath /opt/homebrew/bin/pi -> /opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js
```

`pi --help` reports `--provider`, `--model`, `--system-prompt`, `--mode text|json|rpc`, `--print/-p`, `--continue/-c`, `--resume/-r`, `--session <path|id>`, `--session-dir`, `--no-session`, `--thinking`, and `--extension/-e` [observed command output above]. Pi's docs place extensions at `~/.pi/agent/extensions/` or `.pi/extensions/`, and show the default factory/tool signature with `@earendil-works/pi-coding-agent` ([`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md:1-17,56-107`](\/opt\/homebrew\/lib\/node_modules\/@earendil-works\/pi-coding-agent\/docs\/extensions.md:1-17,56-107)). Pi's session root is `~/.pi/agent/sessions` and it supports partial IDs via `--session` ([`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/sessions.md:1-20`](\/opt\/homebrew\/lib\/node_modules\/@earendil-works\/pi-coding-agent\/docs\/sessions.md:1-20)).

The Pi SDK has the same conceptual `AgentSession.prompt`, `steer`, `followUp`, `sessionId`, `sessionFile`, and event subscription APIs ([`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/sdk.md:66-114`](\/opt\/homebrew\/lib\/node_modules\/@earendil-works\/pi-coding-agent\/docs\/sdk.md:66-114)). Pi's lifecycle uses `agent_settled` for “no retry/compaction/follow-up left” ([`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md:567-580`](\/opt\/homebrew\/lib\/node_modules\/@earendil-works\/pi-coding-agent\/docs\/extensions.md:567-580)), whereas OMP exposes `session_stop` as the continuation-control hook. OMP's loader includes explicit compatibility rewriting for legacy `@mariozechner/*`/`@earendil-works/*` imports, but the alicedev extension should target the OMP package/type names when running under OMP ([`docs/extension-loading.md:224-233`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/extension-loading.md:224-233)). A harness-neutral adapter should use `session_stop` on OMP and `agent_settled` on standalone Pi; do not assume the two event catalogs are identical.

## 5. alicedev extension design sketch

### Ingress contract: preserve message_id without model leakage

The native OMP RPC `prompt` command has only `message`, optional images, and optional streaming behavior; `InputEvent` likewise has no metadata field ([`packages/coding-agent/src/modes/rpc/rpc-types.ts:28-38`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-types.ts:28-38); [`packages/coding-agent/src/extensibility/extensions/types.ts:908-914`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:908-914)). The recommended bridge contract is therefore an extension-only command whose wrapper is consumed locally:

```json
{"id":"ingress-1","type":"prompt","message":"/chat_ingress {\"message_id\":\"tg-123\",\"command\":\"需求\",\"text\":\"build the report\"}"}
```

`chat_ingress` parses the structured argument, stores `{ messageId, originalText }` in session-scoped state, renders the configured frontmatter prompt template, and calls `pi.sendUserMessage(renderedPrompt)`. Because extension commands are consumed before normal agent processing, the JSON wrapper and message ID are not sent to the model ([`packages/coding-agent/src/session/agent-session.ts:6126-6147`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:6126-6147); [`packages/coding-agent/src/modes/rpc/rpc-mode.ts:1130-1161`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:1130-1161)). Keep `/需求 <text>` as a human-facing alias if desired, but it cannot carry an external `message_id` by itself; the bridge should map chat `/需求` to `chat_ingress` [DESIGN CONTRACT].

If preserving the literal `/需求` RPC text is mandatory, the bridge needs a separate ordered sideband command (for example `/chat_bind <message_id>` immediately before `/需求 ...`) or an OMP transport patch. There is no source evidence that arbitrary extra JSON fields survive into an extension [UNKNOWN].

### Exact pseudo-TS skeleton

The following is an implementation contract, not a compiled patch. It uses only real OMP API names/signatures cited above.

```ts
import type {
  ExtensionAPI,
  ExtensionContext,
  ExtensionCommandContext,
  SessionStopEvent,
} from "@oh-my-pi/pi-coding-agent";

type ChatReply =
  | { kind: "text"; payload: string }
  | { kind: "text_template"; payload: string }    // JSON/template args encoded for CLI
  | { kind: "image_template"; payload: string }   // JSON/template args encoded for CLI
  | { kind: "sticker"; payload: string };         // JSON/sticker args encoded for CLI

type Pending = {
  messageId: string;
  originalText: string;
  reminderSent: boolean;
};

export default function alicedev(pi: ExtensionAPI): void {
  const pendingBySession = new Map<string, Pending>();
  const z = pi.zod;

  const sessionKey = (ctx: ExtensionContext): string =>
    ctx.sessionManager.getSessionId();

  pi.on("session_start", (_event, ctx) => {
    // Rebuild pendingBySession from the current branch's latest
    // alicedev.chat.pending / alicedev.chat.consumed custom entries.
    restorePending(ctx, pendingBySession);
  });

  pi.on("session_shutdown", (_event, ctx) => {
    pendingBySession.delete(sessionKey(ctx));
  });

  pi.registerCommand("chat_ingress", {
    description: "Bridge-only structured chat ingress",
    handler: async (raw, ctx: ExtensionCommandContext) => {
      const ingress = parseIngress(raw); // { messageId, command, text }
      if (ingress.command !== "需求") throw new Error("unsupported alicedev command");

      const key = sessionKey(ctx);
      const pending: Pending = {
        messageId: ingress.messageId,
        originalText: ingress.text,
        reminderSent: false,
      };
      pendingBySession.set(key, pending);
      pi.appendEntry("alicedev.chat.pending", pending);

      // Application-owned frontmatter renderer. The model sees only this
      // rendered prompt; it never sees ingress.messageId.
      const prompt = renderRequirementTemplate(ingress.text);
      pi.sendUserMessage(prompt);
    },
  });

  pi.registerTool({
    name: "chat_reply",
    label: "Chat Reply",
    description: "Send the current paseo answer to the originating chat message",
    parameters: z.object({
      // Optional for the model: extension state is authoritative. Keep this
      // field for the wire contract/debugging, but do not trust model text over
      // the session's pending ID.
      message_id: z.string().optional(),
      kind: z.enum(["text", "text_template", "image_template", "sticker"]),
      payload: z.string(),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const key = sessionKey(ctx);
      const pending = pendingBySession.get(key);
      const messageId = pending?.messageId ?? params.message_id;
      if (!messageId) {
        return {
          content: [{ type: "text", text: "No pending chat message for this session." }],
          isError: true,
        };
      }

      // The reverse CLI performs the one-time consume/duplicate check and
      // emits text/template/image/sticker through AstrBot's local interface.
      const result = await pi.exec("alicedev-reply", [
        "--message-id", messageId,
        "--kind", params.kind,
        "--payload", params.payload,
        "--consume",
      ]);
      if (result.exitCode !== 0) {
        return {
          content: [{ type: "text", text: "Chat reply delivery failed; retry is allowed." }],
          isError: true,
        };
      }

      pendingBySession.delete(key);
      pi.appendEntry("alicedev.chat.consumed", { messageId, kind: params.kind });
      return { content: [{ type: "text", text: "Chat reply sent." }] };
    },
  });

  // turn_end is useful for observation/metrics, but its return value is not a
  // continuation control point. Do not rely on returning a reminder here.
  pi.on("turn_end", (_event, _ctx) => {
    // Optional: record assistant turn completion in application telemetry.
  });

  // This is the OMP-native before-yield equivalent. It runs after the turn
  // settles. If chat_reply never consumed the pending ID, schedule one hidden
  // model-visible continuation containing the original request.
  pi.on("session_stop", async (event: SessionStopEvent) => {
    const pending = pendingBySession.get(event.session_id);
    if (!pending || pending.reminderSent) return;

    pending.reminderSent = true;
    pi.appendEntry("alicedev.chat.reminder", {
      messageId: pending.messageId,
      originalText: pending.originalText,
    });
    return {
      continue: true,
      additionalContext:
        `<alicedev-unreplied message="${escapeXml(pending.messageId)}">` +
        `Reply to the original request through chat_reply before yielding: ` +
        `${pending.originalText}</alicedev-unreplied>`,
    };
  });
}
```

Notes on the skeleton:

1. `pi.exec(command, args)` is the actual OMP extension action for invoking a local reverse CLI ([`packages/coding-agent/src/extensibility/extensions/types.ts:1451-1455`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/extensibility\/extensions\/types.ts:1451-1455)). The CLI must be a fixed trusted executable/configured path; do not interpolate untrusted chat text into a shell command string.
2. The tool's `message_id` field is optional because the model must not need to see the ID. The authoritative ID is the pending session state set by `chat_ingress`; the optional parameter is only a compatibility/debug field. OMP's current `ToolDefinition` has no `prepareArguments` hook, so do not design around Pi's separate `prepareArguments` declaration.
3. `session_stop`'s returned `additionalContext` is materialized by OMP as a hidden custom message with type `session-stop-continuation`, then queued for a continuation ([`packages/coding-agent/src/session/agent-session.ts:4133-4146`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:4133-4146)). `reminderSent` prevents an unbounded loop; OMP itself additionally caps continuations at eight ([`packages/coding-agent/src/session/agent-session.ts:4120-4133`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/agent-session.ts:4120-4133)).
4. `message_id` must be escaped in the reminder if it is placed in an XML-like marker. If the original text can contain arbitrary markup, use a fixed delimiter plus length-prefix/JSON encoding rather than interpolating raw text.
5. The local reverse CLI should atomically decide “first consume wins” and return nonzero on duplicate/expired token; the extension only removes its pending entry after exit code 0. The CLI/database semantics are outside this API investigation [DESIGN CONTRACT].
6. If image/sticker output is not model-generated content but a stored template, keep the actual binary/URL handling in the reverse CLI. The tool should pass a bounded template identifier/JSON payload, not arbitrary filesystem paths [DESIGN CONTRACT].

## 6. Recommended integration sequence

1. Start one persisted RPC child with `--mode rpc`, `--system-prompt`, `--provider`, `--model`, and `--thinking`; load the extension by normal discovery or explicit `--extension` ([`docs/rpc.md:16-30`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/rpc.md:16-30); [`docs/cli-reference.md:81-105,130-154`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/cli-reference.md:81-105,130-154)).
2. Wait for `{"type":"ready",...}`. Send a bridge-only `/chat_ingress {...}` prompt. The extension stores the pending ID and starts the rendered `/需求` turn; the wrapper is consumed before agent processing ([`packages/coding-agent/src/modes/rpc/rpc-mode.ts:802-837,1117-1178`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/rpc\/rpc-mode.ts:802-1178)).
3. Stream RPC event frames. When the model calls `chat_reply`, the extension invokes `alicedev-reply --consume`; the CLI sends to AstrBot/Telegram and marks the message ID consumed. The bridge treats the `tool_execution_end`/`turn_end` events as observability and waits for the continuation/settle event, not merely the immediate RPC command response ([`docs/rpc.md:71-83,92-105`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/docs\/rpc.md:71-83,92-105)).
4. If the model yields without consuming, `session_stop` injects one hidden reminder containing the original request and schedules the next model turn. If the CLI failed, leave pending state intact so retry remains possible; if it succeeded, append `alicedev.chat.consumed` and clear pending.
5. On process restart, use the persisted OMP session ID/path only after the JSONL materializes; then either resume through ACP `session/resume` + `session/prompt`, or use the one-shot `omp --resume "$SESSION_ID" -p` queue-worker path. Do not claim that `omp --resume` attaches to a live process ([`packages/coding-agent/src/session/session-manager.ts:2096-2109`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/session\/session-manager.ts:2096-2109); [`packages/coding-agent/src/modes/acp/acp-agent.ts:731-740`](\/Users\/mouriya\/Ext\/code\/oh-my-pi\/packages\/coding-agent\/src\/modes\/acp\/acp-agent.ts:731-740)).

## Open/explicitly unresolved items

- **Inbound metadata transport:** OMP does not expose `message_id` in `InputEvent` or RPC prompt frames. The bridge-only `chat_ingress` command is the recommended contract; a literal `/需求` plus hidden metadata requires a separate sideband or a harness patch [UNKNOWN until alicedev's reverse CLI contract is fixed].
- **Frontmatter schema:** OMP's extension API does not prescribe alicedev's frontmatter template fields. `renderRequirementTemplate` belongs to application configuration; define its exact fields in the alicedev architecture [UNKNOWN here].
- **Reverse CLI/database protocol:** exact command name, payload schema, one-time consume behavior, and AstrBot/Telegram output adapters are outside this OMP API slice [UNKNOWN here].
- **Standalone Pi fallback:** local Pi has analogous `AgentSession` queueing and `agent_settled`, but no OMP `session_stop` contract in the inspected docs. A shared extension must feature-detect the host or use separate OMP/Pi adapter files; the OMP implementation above is authoritative for paseo's OMP mode ([`/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/docs/extensions.md:567-580`](\/opt\/homebrew\/lib\/node_modules\/@earendil-works\/pi-coding-agent\/docs\/extensions.md:567-580)).
