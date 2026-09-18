# Paseo daemon/API facts for alicedev

Read-only investigation of `/Users/mouriya/Ext/code/paseo` and the local Oh My Pi checkout. No Paseo files were edited; no formatter, linter, build, or test was run. Source citations below are the observations an implementation can rely on. Items marked **[INFERENCE]** are design conclusions rather than runtime-proven behavior.

## 1. Provider integration: OMP, Pi, and ACP

### Native provider choices

- Paseo has direct/native adapters for `claude`, `codex`, `opencode`, `pi`, and `omp`; its provider guide explicitly lists `pi` and `omp` among existing direct providers (`docs/providers.md:52-56`).
- Pi is process-backed and requires an installed `pi` executable. Paseo invokes Pi through `pi --mode rpc`; the server does not embed Pi's runtime (`docs/providers.md:64-74`).
- OMP is a first-class provider, but is disabled by default. Its native launch contract is `omp --mode rpc-ui`; it supports history/imports, permissions, slash-command discovery, and native Paseo host tools (`docs/providers.md:76-78`). The OMP runtime constructs its process argv and RPC/UI transport in `packages/server/src/server/agent/providers/omp/runtime.ts:86-147`.
- Oh My Pi itself exposes both `omp --mode rpc` and `omp acp` over stdio (`/Users/mouriya/Ext/code/oh-my-pi/README.md:494-496,532-550`). Its CLI documents `--mode acp` and the `acp` subcommand as an ACP JSON-RPC server (`/Users/mouriya/Ext/code/oh-my-pi/docs/cli-reference.md:193-211`).

**Recommendation:** use Paseo's native `omp` adapter when OMP host-tool integration, OMP subagents, imports, permissions, and `rpc-ui` behavior are wanted. Use native `pi` when the provider is Pi. Do not assume that an ACP wrapper is equivalent to the native OMP adapter: ACP is a viable fallback, but it loses provider-specific Paseo behavior unless the ACP agent exposes equivalent capabilities.
- The wire `AgentSessionConfig` has no generic `effort` field. The closest protocol-level control is `thinkingOptionId`; provider-specific values belong in the provider's validated `providerOptions` (`packages/protocol/src/messages.ts:481-493`). A frontmatter `effort` value therefore needs an explicit mapping per provider, not blind forwarding.

### Custom ACP configuration

- Custom provider entries are typed around `extends`, command mode (`default`/`append`/`replace`), optional environment, and tool restrictions; `extends: "acp"` is a supported custom-provider base (`packages/protocol/src/provider-config.ts:5-29,51-90`). The public custom-provider guide also says `extends` can target a built-in provider or `"acp"` (`public-docs/custom-providers.md:51-77`).
- The custom ACP registry requires a non-empty command and creates `GenericACPAgentClient` with command, env, provider ID/label, and params (`packages/server/src/server/agent/provider-registry.ts:755-817`). The generic adapter treats argv[0] as the executable and the remaining elements as arguments (`packages/server/src/server/agent/providers/generic-acp-agent.ts:40-100`).
- **[INFERENCE, not runtime-tested]** An OMP ACP profile can therefore be represented as:

  ```json
  {
    "agents": {
      "providers": {
        "omp-acp": {
          "extends": "acp",
          "label": "OMP ACP",
          "command": ["omp", "acp"]
        }
      }
    }
  }
  ```

  This follows Paseo's generic ACP command contract and OMP's documented `omp acp` command. Prefer native `omp` unless this wrapper is deliberately desired.

### Plugin ACP/direct provider boundary

- A server plugin registers a provider with `server.registerProvider`. `ProviderRegistration` requires `id`, `label`, and `connect`; the resulting connection exposes `send`, `onEvent`, and `close` (`packages/plugin/src/server/provider.ts:28-54`).
- Provider session config includes `cwd`, inherited/explicit `env`, `systemPrompt`, `mcpServers`, `toolPolicy`, `model`, `mode`, `thinkingOption`, `settings`, `providerOptions`, `title`, and `persist` (`packages/plugin/src/server/provider.ts:80-93`). Provider inputs include catalog/sessions, `session.open`, `session.prompt`, interrupt, permission, configure, revert, archive, unarchive, and close (`packages/plugin/src/server/provider.ts:217-251`). Provider events include open/ready/closed/runtime failure, persistence, prompt results, turn state, usage, config, commands, permissions, notices, and timeline items (`packages/plugin/src/server/provider.ts:525-569`).
- `runAcpProvider` accepts exactly one executable argv or one SDK connector. It validates that choice and returns a `ProviderRegistration` (`packages/plugin/src/server/acp.ts:12-25,104-121`). The command-backed ACP connection spawns the child with inherited plus configured environment and piped stdio, then initializes ACP (`packages/plugin/src/server/acp-internal/connection.ts:372-415`).
- Minimal plugin registration is demonstrated by `plugin-examples/provider-acp-transformer/index.server.ts:1-17`; direct provider registration is `plugin-examples/provider-direct/index.server.ts:1-7`.
- **[INFERENCE]** A plugin that only needs to launch an ACP-speaking binary should use `runAcpProvider({ id, label, command: ["binary", "args"] })`; implementing `ProviderRegistration` directly is needed only for a non-ACP process/protocol or custom session bridge.

## 2. Daemon transport, authentication, and wire API

### Transport and authentication

- The externally usable control plane is the daemon WebSocket at `/ws`. The server constructs its `WebSocketServer` with `path: "/ws"` (`packages/server/src/server/websocket-server.ts:802-825`).
- A configured daemon password is checked during the WebSocket upgrade. The client sends both `Authorization: Bearer <password>` and the WebSocket subprotocol `paseo.bearer.<password>` (`packages/client/src/daemon-client.ts:1223-1231`); the server extracts and validates the bearer subprotocol before attaching the socket (`packages/server/src/server/websocket-server.ts:901-923`). The CLI obtains the password from `PASEO_PASSWORD` unless a `tcp://...` URI embeds one (`packages/cli/src/utils/client.ts:253-260`). Never put the actual password in a command transcript or source file.
- After upgrade, the client must send a `hello` within 15 seconds. Protocol version must be `1`; the hello requires non-empty `clientId`, and `clientType` is one of `mobile`, `browser`, `cli`, `mcp`, or `hub` (`packages/server/src/server/websocket-server.ts:493-500,1240-1303,1492-1577`; `packages/protocol/src/messages.ts:7135-7161`). The server sends `server_info` after accepting hello (`packages/server/src/server/websocket-server.ts:1552-1577`).
- HTTP is not a generic session CRUD API. After static UI mounting and bearer middleware, the server exposes `/api/health` and `/api/status` (`packages/server/src/server/bootstrap.ts:759-789`). `/api/health` is intentionally bearer-free; the auth helper identifies it as a bypass route (`packages/server/src/server/auth.ts:90-128`). `/mcp/agents` is a separate stateless MCP POST endpoint, not the normal session-control protocol; GET/DELETE have no session meaning (`packages/server/src/server/bootstrap.ts:1435-1520`). Use WebSocket for alicedev's agent lifecycle and messages.

### WebSocket envelope and message sequence

All session operations use an outer envelope:

```json
{"type":"session","message":{ "type":"<session_message>", "requestId":"..." }}
```

The protocol validates this envelope and the corresponding inbound/outbound message schemas (`packages/protocol/src/messages.ts:7168-7190`). A minimal end-to-end sequence is:

1. **Hello:**

   ```json
   {
     "type": "hello",
     "clientId": "alicedev-<instance>",
     "clientType": "cli",
     "protocolVersion": 1,
     "appVersion": "alicedev"
   }
   ```

2. **Create an agent:** wrap `create_agent_request` with `requestId`, `config.provider`, `config.cwd`, and optional `config.model`, `config.modeId`, `config.thinkingOptionId`, `config.systemPrompt`, `config.providerOptions`, `workspaceId`, `env`, `title`, `initialPrompt`, labels, and attachments. The exact schema is `packages/protocol/src/messages.ts:481-493,1681-1702`. Success is a `status` payload with `status: "agent_created"`, the full snapshot, `agentId`, and `requestId`; failure is `status: "agent_create_failed"` with error/errorCode (`packages/protocol/src/messages.ts:3631-3659`).
- For retry-safe creation, do not send `idempotencyKey` together with `initialPrompt`: the server rejects that combination and expects the initial prompt to be sent separately with a stable message ID (`packages/server/src/server/session.ts:3568-3626`; `packages/protocol/src/messages.ts:1681-1702`). If the bridge uses an idempotency key, create first, persist the returned ID/handle, then send the initial turn.
3. **Subscribe to live timeline output:** send `agent.timeline.set_subscription.request` with the target agent ID(s) and a request ID (`packages/protocol/src/messages.ts:1837-1841`). The response carries the active IDs (`packages/protocol/src/messages.ts:4595-4602`). Subsequent `agent_stream` messages carry `agentId`, event, timestamp, sequence, and epoch; timeline events are provider `AgentStreamEvent` entries whose `event.type` is `"timeline"` and whose item can be an assistant message (`packages/protocol/src/messages.ts:3952-3971`; `packages/server/src/server/agent/agent-sdk-types.ts:419-451`).
4. **Send a user turn:** wrap `send_agent_message_request` with `requestId`, `agentId`, `text`, and a stable client-generated `messageId`. Optional `activeTurnBehavior`, images, and attachments are supported (`packages/protocol/src/messages.ts:1395-1405`). The correlated response is `send_agent_message_response` with `accepted` and nullable `error` (`packages/protocol/src/messages.ts:4700-4708`). A stable message ID is important for bridge retries/deduplication; the SDK generates one when absent (`packages/client/src/daemon-client.ts:3189-3223`). The server defaults omitted active-turn behavior to `interrupt`, so the bridge should serialize chat sends or deliberately choose the supported steering behavior (`packages/server/src/server/session.ts:7570-7654`; `packages/protocol/src/messages.ts:1240-1241`).
5. **Wait for a terminal result (optional):** send `wait_for_finish_request` with `agentId` and optional `timeoutMs`; response status is `idle`, `error`, `permission`, or `timeout`, with final snapshot/error/lastMessage (`packages/protocol/src/messages.ts:1407-1413,4710-4718`).
6. **Repair after reconnect/gap:** fetch authoritative history with `fetch_agent_timeline_request` (`direction: "tail"|"before"|"after"`, cursor, limit, projection) and merge based on `epoch`/sequence metadata (`packages/protocol/src/messages.ts:1801-1813,4457-4481`). The high-level client exposes the same operation and a live timeline subscription (`packages/client/src/daemon-client.ts:2919-2955,3061-3085`).

`fetch_agents_request` can list active agents, filter/sort/page, and request a directory subscription; its response contains entries, page info, and optional subscription ID (`packages/protocol/src/messages.ts:1277-1303,4015-4024`). Agent updates are upsert/remove snapshots, while status messages carry an agent snapshot (`packages/protocol/src/messages.ts:3933-3977`). This is the suitable discovery path for mapping a chat's stored `agentId` to a still-existing Paseo session.

For explicit project/workspace placement, use the normal project/workspace requests before agent creation. The protocol has `open_project_request`, `project.add.request`, and `project.create_directory.request`; workspace creation takes a discriminated directory/worktree `source` and returns `workspace.create.response` (`packages/protocol/src/messages.ts:2496-2517,2552-2588,4658-4667`). `create_agent_request.workspaceId` should reference the returned workspace; the CLI's bare `agent run` performs this workspace resolution automatically (`packages/cli/src/commands/agent/run.ts:535-583`).

### SDK/CLI equivalent

The CLI and `@getpaseo/client` use the same WebSocket protocol. The client methods are concrete references for a bridge implementation: `createAgent` sends the create request and waits for `agent_created`/`agent_create_failed` (`packages/client/src/daemon-client.ts:2549-2602`); `sendAgentMessage` sends and rejects if not accepted (`packages/client/src/daemon-client.ts:3189-3223`); `waitForFinish` sends the wait request (`packages/client/src/daemon-client.ts:5377-5398`); and timeline fetch/subscription are exposed as above.

A source-grounded CLI smoke sequence (not executed during this investigation) is:

```bash
# The daemon must be running and provider `omp` must be enabled/configured.
export PASEO_PASSWORD='[load from the deployment secret store; do not print]'
paseo daemon start

# --background returns a JSON result containing agentId when --json is enabled.
AGENT_ID="$({ paseo agent run --background --provider omp --cwd /workspace --json \
  'You are the paseo session for alicedev. Acknowledge the request.'; } | jq -r '.agentId')"

paseo agent send "$AGENT_ID" 'Reply with the result for the chat bridge.' --no-wait --json
paseo agent wait "$AGENT_ID" --timeout 60s --json
paseo agent logs "$AGENT_ID" --tail 20
```

The run command supports background/provider/model/mode/cwd/workspace/env/labels and creates a workspace when none is supplied (`packages/cli/src/commands/agent/run.ts:21-85,535-583,701-725`). The send command supports `--no-wait` and otherwise waits for finish (`packages/cli/src/commands/agent/send.ts:37-46,200-230`). The wait command accepts duration syntax such as `60s` (`packages/cli/src/commands/agent/wait.ts:29-33,59-80`), and logs supports `--tail`/`--follow` (`packages/cli/src/commands/agent/logs.ts:12-19`).

The CLI command registry has `ls`, `run`, `import`, `attach`, `logs`, `open`, `stop`, `delete`, `send`, `inspect`, `wait`, `mode`, `archive`, `reload`, `detach`, and `update`; there is no `resume` or `close` CLI command (`packages/cli/src/commands/agent/index.ts:26-110`).

## 3. Lifecycle, resume, and the 12-hour idle requirement

### Existing lifecycle semantics

- Agent statuses are `initializing -> idle -> running -> idle/error`; `closed` means a persisted, resumable record with no live provider runtime (`docs/agent-lifecycle.md:5-13`). A closed agent retains identity, persistence handle, timeline, workspace, labels, title, and timestamps; opening or prompting it runs `ensureAgentLoaded()` under the same agent ID (`docs/agent-lifecycle.md:15-22`).
- Idle agents remain resident indefinitely. Runtime closure occurs only through explicit lifecycle actions such as archive, replacement, reload, workspace teardown, or daemon shutdown (`docs/agent-lifecycle.md:24-29`). There is no built-in 12-hour idle close setting in the searched server/protocol/docs paths (search for `idleTimeout|idle.timeout|idle.*timeout|timeout.*idle` returned only unrelated test/speech idle symbols, not an agent idle policy).
- `close_items_request` is a public “close” name, but it archives agent items (soft delete) and kills terminal items; it is not a resumable runtime-close operation (`packages/server/src/server/session.ts:2966-3007`; `packages/protocol/src/messages.ts:956-973,4931-4938`). This distinction matters for the 12-hour requirement.
- Archive is a soft delete: it writes `archivedAt`, closes runtime, removes the agent from active lists, and cascades to managed children (`docs/agent-lifecycle.md:87-104`). `cancel_agent_request`/CLI `stop` interrupts a running turn and is explicitly a no-op for idle agents (`packages/cli/src/commands/agent/stop.ts:25-30,98-109`); it is not runtime closure.

### Resume and close implications for alicedev

- The wire protocol has `resume_agent_request` with a persisted `handle` and optional partial config overrides (`packages/protocol/src/messages.ts:1749-1754`). The client `resumeAgent(handle, overrides)` waits for `agent_resumed` (`packages/client/src/daemon-client.ts:2822-2851`). A snapshot's `persistence` handle is nullable and is part of `AgentSnapshotPayload` (`packages/protocol/src/messages.ts:854-883`).
- There is **no public close-agent wire operation** in the current protocol/client/CLI search. The exact search for `close_agent_request|CloseAgentRequestMessageSchema` across `packages/protocol/src/messages.ts`, `packages/client/src/daemon-client.ts`, and `packages/cli/src/commands/agent` returned `No matches found`. The provider-internal plugin input does have `session.close` (`packages/plugin/src/server/provider.ts:217-251`), but that is a provider adapter operation, not a daemon agent close RPC.
- Wire snapshots expose `updatedAt` and `lastUserMessageAt`, but not the persisted data-model `lastActivityAt` field (`packages/protocol/src/messages.ts:854-883`; `docs/data-model.md:81-99`). A sidecar should define its idle timestamp explicitly (for example, last turn end persisted by alicedev) rather than assuming `lastActivityAt` arrives over the wire.
- Therefore the requested “close after 12h idle but retain resumable session” cannot be implemented by simply calling an existing public `PaseoApi` method. `PaseoAgentHandle` exposes `refresh`, `send`, `run`, `waitForFinish`, `archive`, `detach`, and subscriptions, but no close (`packages/client/src/index.ts:313-364`). **[INFERENCE]** The clean implementation is a small Paseo fork/API addition for an authenticated `close_agent_request` (or an explicitly supported plugin lifecycle API) that calls the daemon's existing internal close path while preserving the record. Archiving and immediately resuming would not be equivalent: archive intentionally changes visible lifecycle state and is a soft delete.
- A sidecar can still detect/schedule idle closure: plugin lifecycle hooks include `agent.turn_started`, `agent.turn_ended` (with outcome and timeline), permission events, create, and archive (`packages/plugin/src/server/lifecycle.ts:47-65`). `PaseoApi.agents.subscribe` exposes agent upsert/remove updates and `PaseoAgent` snapshots expose status/timestamps (`packages/client/src/index.ts:356-364`; `packages/protocol/src/messages.ts:854-883`). Use a persistent timer table keyed by `agentId` so a plugin restart can reconstruct timers. The plugin docs warn not to await observer callbacks inside agent mutations because that can deadlock; schedule side effects asynchronously (`docs/plugins.md:286-295`).
- The low-level session dispatch also applies operation permissions before handlers; a raw bridge client must receive the needed workspace read/write/manage permissions (and, where applicable, `hub.execute`) or it will receive `rpc_error` access-denied responses (`packages/server/src/server/session.ts:1897-1965`; `packages/server/src/server/authorization/operation-permissions.ts:8-205`). Owner/default local clients receive all permissions by default (`packages/server/src/server/authorization/index.ts:23-38`).

## 4. Plugin and extension boundaries

- `PluginServerContext` exposes only lifecycle `on`/`before`, `registerSettings`, typed `handle` (plugin RPC), and `registerProvider` (`packages/plugin/src/server/contracts.ts:8-26`). A contribution returns cleanup; the plugin process owns its daemon session (`docs/plugins.md:236-254`).
- There is no documented HTTP route registration method. This is a hard boundary for alicedev: a Paseo server plugin cannot add an arbitrary `/alicedev/...` daemon HTTP endpoint through the exported plugin API. Use the normal daemon WebSocket/CLI from AstrBot, or define typed plugin RPC for Paseo app/plugin clients; do not design around an unobserved Express route hook. **[INFERENCE]** The external AstrBot process should remain an independent bridge client rather than depending on plugin RPC.
- `server.handle` is appropriate for plugin-specific requests from a client surface, but normal Paseo operations should use the supplied `paseo` API (`docs/plugins.md:249-254`). The `PaseoApi` contains projects, workspaces, agents, terminals, providers, and daemon config (`packages/client/src/index.ts:452-459`).
- Lifecycle hooks are suitable for the 12-hour sidecar's observation and bookkeeping, but they are not an idle event. Derive idle from `agent.turn_ended` plus agent snapshots; account for permission/error/closed transitions. `agent.turn_ended` contains a complete timeline snapshot for the turn (`packages/plugin/src/server/lifecycle.ts:47-54`).
- If the plugin provides a new agent implementation rather than just lifecycle bookkeeping, direct providers must implement the session boundary and event contract. ACP providers should use `runAcpProvider`; do not create a second callback/tool API (`docs/plugins.md:297-332`; `packages/plugin/src/server/acp.ts:104-121`).

## 5. Self-hosted runtime, proxy base paths, and deep links

### Self-hosted manifest and WebSocket path

- Self-hosted builds are gated by `EXPO_PUBLIC_PASEO_SELFHOSTED=true` (`packages/app/src/fork-features/self-hosted/runtime.ts:40-47`). The app fetches a no-cache manifest from `/_paseo/hosts.json` (`packages/app/src/fork-features/self-hosted/runtime.ts:82-88`). Each strict entry has `{ id, label, basePath }`; IDs are lowercase slugs and `basePath` must be exactly `/daemons/<id>` (`packages/app/src/fork-features/self-hosted/runtime.ts:9-36`).
- The manifest entry is persisted as a direct TCP connection with `basePath` (`packages/app/src/fork-features/self-hosted/runtime.ts:121-132`). Host runtime passes `basePath` and TLS into WebSocket URL construction (`packages/app/src/runtime/host-runtime.ts:603-612`). The shared endpoint builder maps no base path to `/ws`, and a valid proxy path to `<basePath>/ws`, e.g. `wss://app.example:443/daemons/alpha/ws` (`packages/protocol/src/daemon-endpoints.ts:165-187`).
- The browser self-hosted target uses the page's HTTP(S) origin as daemon endpoint; with `EXPO_PUBLIC_LOCAL_DAEMON=self-hosted`, the local override resolves to `window.location.host` (`packages/app/src/fork-features/self-hosted/runtime.ts:54-75,91-107`). The standard Docker image uses this setting for a single daemon origin (`docs/docker.md:18-21`).
- Same-origin multi-daemon deployment requires the external deployment layer to serve `/_paseo/hosts.json` and route every `/daemons/<id>/` path to its daemon (`docs/docker.md:124-137`). **[INFERENCE]** `basePath` is a daemon transport/proxy prefix, not a UI route prefix: the app's UI route builders remain rooted at `/h/...`; the proxy must separately serve app/static routes and daemon `/daemons/<id>/` routes.

### Routes and deep links

- Without workspace context, the browser route is `/h/<encoded-serverId>/agent/<encoded-agentId>` (`packages/app/src/utils/host-routes.ts:267-288,375-396`). With workspace context, `buildHostAgentDetailRoute` emits `/h/<serverId>/workspace/<encoded-workspaceId>?open=agent:<agentId>` (`packages/app/src/utils/host-routes.ts:349-396`).
- The workspace route reads `open=agent:<agentId>`, prepares/pins the agent tab after workspace hydration, then removes the query from the browser URL (`packages/app/src/app/h/[serverId]/workspace/[workspaceId]/index.tsx:102-172`). Thus the query is a one-time navigation intent, not a durable public URL state.
- The native custom URI builder emits `paseo:/h/<server>/<agent>` and the parser accepts only protocol `paseo:`, hostname `h`, no auth/port/query/hash, and exactly three path segments (`packages/protocol/src/agent-deep-link.ts:19-61`). Tests show URL serialization such as `paseo://h/server%2Fmain/agent/agent%20123` (`packages/protocol/src/agent-deep-link.test.ts:9-23`).
- For alicedev links, store the exact Paseo `serverId` (often a managed self-host ID such as `selfhosted:alpha`) and `agentId` in DuckDB. **[INFERENCE]** Generate the workspace form when `workspaceId` is known; otherwise use the direct agent route. Do not prepend `/daemons/<id>` to `/h/...` unless the chosen reverse proxy explicitly mounts the whole app under that prefix; Paseo source itself does not do so.

## 6. Docker/runtime constraints and Arch adaptation

### Existing image contract

- The current image is **not Arch-based**: it uses configurable `node:22-bookworm-slim` for source packaging and runtime (`docker/base/Dockerfile:3-35`). The source stage runs `npm ci` and packs the highlight, relay, protocol, client, plugin, server, and CLI workspace artifacts; the runtime installs those tarballs globally and checks the supervisor entrypoint (`docker/base/Dockerfile:17-33,65-73`).
- Runtime defaults are `HOME=/home/paseo`, `PASEO_HOME=/home/paseo/.paseo`, `PASEO_LISTEN=0.0.0.0:6767`, bundled UI enabled, JSON/info logs, and provider config roots under `/home/paseo`; the Debian package set is bash, ca-certificates, curl, git, gosu, lbzip2, openssh-client, procps, and tini (`docker/base/Dockerfile:37-63`).
- The image creates uid/gid `1000:1000` user `paseo`, creates `/workspace` and all state/config directories, sets `/workspace` as workdir, exposes 6767, volumes `/home/paseo`, health-checks `GET /api/health`, and starts through tini (`docker/base/Dockerfile:75-110`).
- The entrypoint creates/chowns state directories when started as root, then runs the daemon and explicitly invoked commands as user `paseo` via `gosu`; it warns if `PASEO_PASSWORD` is missing (`docker/base/rootfs/usr/local/bin/paseo-docker-entrypoint:32-78`).
- The documented compose contract publishes `6767:6767`, mounts persistent `/home/paseo` and code `/workspace`, and sets a placeholder password that must be changed for any network-reachable deployment (`docker/docker-compose.example.yml:5-21`). The private image name in the docs/workflow is `registry.237575.xyz/paseo/paseo:latest` (`docs/docker.md:23-47`; `.github/workflows/docker.yml:29-31`).
- The base image intentionally omits agent CLIs. The child-image example installs Claude Code, Codex, and OpenCode; OMP and Pi must be added to the image or mounted/runtime-installed on the same PATH as the `paseo` user (`docs/docker.md:75-103`; `docker/Dockerfile.agents.example:8-17`). Agent credentials/config persist under `/home/paseo`, and provider environment variables are passed through to launched agents (`docs/docker.md:97-103`).
- Reverse proxies must forward ordinary HTTP and WebSocket upgrades to port 6767 and preserve `Host`; `PASEO_HOSTNAMES` allows DNS names not accepted by default (`docs/docker.md:139-178`).

### Arch-based container adaptation

- **Observed constraint:** replacing only `FROM node:22-bookworm-slim` with `archlinux` is insufficient because the current Dockerfile uses `apt-get`, Debian package names, and a Debian source-builder/runtime pairing (`docker/base/Dockerfile:51-63`).
- **[INFERENCE]** Preserve the image contract while porting to Arch: use a Node/npm-capable Arch builder for `npm ci` and workspace packing, then an Arch runtime with the equivalent packages for shell/CA certificates/curl/git/privilege drop/SSH/process tools/tini. Keep Node's builder/runtime ABI aligned if any dependency contains native code; do not silently build native modules on Debian and run them on Arch.
- **[INFERENCE]** Keep the existing `/home/paseo` and `/workspace` mount semantics, uid/gid 1000, `PASEO_LISTEN=0.0.0.0:6767`, `/api/health` healthcheck, and root-to-non-root entrypoint behavior. The Arch image should install or copy the `paseo` entrypoint and supervisor entrypoint in the same locations, then add OMP/Pi executables in the child layer.
- The normal single-daemon deployment does not need the self-hosted multi-daemon flag: it uses the daemon's own origin and `EXPO_PUBLIC_LOCAL_DAEMON=self-hosted`; set `EXPO_PUBLIC_PASEO_SELFHOSTED=true` only when the deployment actually serves the manifest and `/daemons/<id>/` proxy (`docs/docker.md:124-137`).
- No container/registry/proxy smoke test was run here. Actual registry availability, Arch package names, OMP/Pi installation success, and reverse-proxy WebSocket behavior remain runtime-validation work for the deployment owner.

## Architectural consequences for alicedev

1. Keep the chat bridge as a normal Paseo WS/CLI client. Use the stable `agentId`, `workspaceId`, and `serverId` as DuckDB keys; subscribe to the timeline and repair from `fetch_agent_timeline` after reconnect.
2. Map frontmatter `effort` deliberately: there is no generic wire field, so use a provider-specific `thinkingOptionId`/provider option mapping and record the chosen mapping with the session.
3. Prefer native `omp` or `pi` provider configuration over ACP. If ACP is selected, treat `command: ["omp", "acp"]` as an explicitly [INFERENCE]-level compatibility path until a live ACP handshake is tested.
4. Implement prompt-template expansion into `create_agent_request.config.systemPrompt`/`initialPrompt` rather than embedding chat routing in provider options. `systemPrompt` belongs to the session config; the chat message belongs to `initialPrompt` or `send_agent_message_request` (`packages/protocol/src/messages.ts:481-493,1395-1405,1681-1702`).
5. Treat the 12-hour “close but resumable” behavior as an upstream API gap, not as `stop` or `archive`. Add/land a narrowly scoped close-runtime operation or explicitly change the product requirement to archive semantics before implementation.
6. Keep the TLS/one-time-token gateway outside the Paseo plugin boundary. The documented plugin API has no arbitrary HTTP route registration; the gateway should proxy `/ws` (or `/daemons/<id>/ws`) and enforce its own one-time token before forwarding Paseo's bearer credentials.
