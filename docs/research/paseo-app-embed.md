# Paseo app embed-mode facts

## Scope and decisive observation

This is a read-only implementation survey of the Expo web app in `/Users/mouriya/Ext/code/paseo/packages/app`. The decisive layout boundary is `AppContainer`: `AppWithSidebar` decides whether app chrome is enabled, and `AppContainer` mounts the outer sidebar plus the workspace content. `AppWithSidebar` computes `shouldShowAppChrome` from the current pathname, store readiness, and whether the route is a known host route (`packages/app/src/app/_layout.tsx:869-884`). `AppContainer` receives `chromeEnabled`, computes desktop/compact presentation, mounts `SidebarChrome`, and renders the children/content surface (`packages/app/src/app/_layout.tsx:461-633`).

The intended fork feature is a URL-selected `embed=1` mode that keeps the right workspace surface (tabs, conversation, composer controls, permission cards) but removes navigational chrome and escape routes. No source files were edited and no formatter, lint, test, or build was run in this investigation.

## 1. Existing component and route tree

### Application shell

- `RootLayout` installs the root providers and app tree (`packages/app/src/app/_layout.tsx:986-1021`). `AppShell` composes `AppWithSidebar`, route navigation bridging, and `RootStack` (`packages/app/src/app/_layout.tsx:936-947`).
- `AppContainer` owns the outer chrome boundary. Desktop `SidebarChrome` wraps `LeftSidebar`; compact layouts use `CompactExplorerSidebarHost`; the content is wrapped in `WindowChromeRegion` and gesture handling (`packages/app/src/app/_layout.tsx:494-578,627-633`).
- `SidebarChrome` maps visibility to the `LeftSidebar` and the sidebar model provider (`packages/app/src/app/_layout.tsx:636-654`).
- The root stack contains index/welcome/settings/new/open-project/sessions/schedules and host routes (`packages/app/src/app/_layout.tsx:892-922`).

### Host and workspace route tree

- A host layout at `/h/[serverId]` resolves startup state and provides `HostRouteProvider`; its stack includes the host index, workspace route, agent deep-link route, sessions, open-project, settings, and plugin surfaces (`packages/app/src/app/h/[serverId]/_layout.tsx:19-53`).
- The normal workspace route is `/h/<serverId>/workspace/<workspaceId>`. `buildHostWorkspaceRoute` encodes both IDs and emits that shape (`packages/app/src/utils/host-routes.ts:290-309,349-360`).
- A workspace open intent is encoded as `?open=<encoded-intent>`; `buildHostWorkspaceOpenRoute` emits `?open=...` (`packages/app/src/utils/host-routes.ts:362-373`). Agent intent is the `agent:<agentId>` variant (`packages/app/src/utils/host-routes.ts:122-127,148-150`).
- The workspace index reads `open` globally, derives `recoveryAgentId`, waits for workspace hydration, calls `prepareWorkspaceTab`, then clears the consumed `open` parameter (`packages/app/src/app/h/[serverId]/workspace/[workspaceId]/index.tsx:96-194`). The canonical URL cleanup only removes route-echo parameters (`serverId`, `workspaceId`, and `pop`) and preserves unrelated search parameters (`packages/app/src/utils/host-routes.ts:312-347`). This makes a known-workspace embed URL viable: `/h/<serverId>/workspace/<workspaceId>?open=agent%3A<agentId>&embed=1`.
- The generic deep-link route is `/h/<serverId>/agent/<agentId>`; the parser recognizes that exact form (`packages/app/src/utils/host-routes.ts:267-287`). The route waits for host connectivity, reads a cached workspace ID or fetches the agent, and when resolved calls `navigateToAgent` (`packages/app/src/app/h/[serverId]/agent/[agentId].tsx:21-113`).
- `navigateToAgent` uses the known workspace when available, otherwise emits the generic agent route (`packages/app/src/utils/navigate-to-agent/resolve.ts:24-44`). `navigateToWorkspace` emits either a workspace route with `?open=agent:...` while hydration is deferred or a plain workspace route once the target can be prepared (`packages/app/src/stores/navigation-active-workspace-store/navigation.ts:85-127`). **[INFERENCE]** An arbitrary query such as `embed=1` can be lost during the generic-agent-to-workspace redirect because the route builders called by these navigation paths only build `open`/workspace parameters; they do not copy arbitrary search parameters (`packages/app/src/app/h/[serverId]/agent/[agentId].tsx:94-113`, `packages/app/src/utils/navigate-to-agent/resolve.ts:24-44`, `packages/app/src/stores/navigation-active-workspace-store/navigation.ts:117-126`). Prefer the known-workspace URL above, or make the agent redirect explicitly preserve the embed flag.

### Right/main workspace surface

- `WorkspaceScreen` owns the workspace shell, workspace header, desktop tabs, split layout, and panel content (`packages/app/src/screens/workspace/workspace-screen.tsx:1536-1567`).
- On desktop, it builds `desktopSplitContent` with `SplitContainer`, passing the workspace header renderer, explorer header action, focus state, pane callbacks, and content model (`packages/app/src/screens/workspace/workspace-screen.tsx:3950-3989`).
- The center-column fallback renders the workspace header (when not using the desktop split), the mobile or desktop tabs row, and `WorkspacePanelContent` (`packages/app/src/screens/workspace/workspace-screen.tsx:4036-4093`). The full workspace is then wrapped with panel/floating-host providers (`packages/app/src/screens/workspace/workspace-screen.tsx:4095-4136`).
- `SplitContainer` renders the main header and split-tree panes, and separately mounts the explorer-sidebar dock when not in focus mode (`packages/app/src/components/split-container.tsx:645-738`). Its public props already include `focusModeEnabled` and `onExitFocusMode` (`packages/app/src/components/split-container.tsx:95-139`).
- The existing focus projection isolates the focused pane when focus mode is enabled; if no valid focused pane exists, it falls back to the full root (`packages/app/src/components/split-container-focus.ts:3-15`). The explorer dock is suppressed by `!focusModeEnabled` (`packages/app/src/components/split-container.tsx:369-444`).
- `WorkspacePaneContent` resolves the panel component and renders it inside `PaneProvider`/focus context (`packages/app/src/screens/workspace/workspace-pane-content.tsx:18-22,91-164`).
- `AgentPanel`/`AgentConversationPanel` assemble the agent stream, composer, pending-permission overlays, and toast surface (`packages/app/src/panels/agent-panel.tsx:381-449,1213-1444`). `AgentComposerSection` renders `Composer` (`packages/app/src/panels/agent-panel.tsx:1546-1732`).
- `Composer` preserves left-side `AgentControls` when controls are present and renders the stable message input with left and right content (`packages/app/src/composer/index.tsx:307-330,2143-2153,2293-2404`).
- `AgentControls` exposes provider/model selectors, thinking/effort selector, mode control, and feature controls. The desktop controls have stable test IDs `agent-provider-selector`, `agent-thinking-selector`, and `agent-controls-features` (`packages/app/src/composer/agent-controls/index.tsx:891-1104`). The controlled component determines whether model/thinking/features/mode controls are present (`packages/app/src/composer/agent-controls/index.tsx:520-569`).
- The agent stream renders pending permission cards (`packages/app/src/agent-stream/view.tsx:141-155`). `PermissionRequestCard` provides allow/deny actions and responds through `client.respondToPermissionAndWait` (`packages/app/src/agent-stream/view.tsx:1383-1513`). Embed mode must retain this stream area and its actions.

## 2. Existing mechanisms and URL constraints

### Existing focus mode is useful but insufficient as-is

- Focus state is a persisted panel-store boolean with toggle/exit actions (`packages/app/src/stores/panel-store/index.ts:58-110,131,146-156`).
- Workspace focus mode hides the desktop workspace header (`packages/app/src/screens/workspace/workspace-screen.tsx:1381-1385`) and the explorer dock (`packages/app/src/components/split-container.tsx:442-444`).
- App-level desktop sidebar mounting also excludes workspace focus mode (`packages/app/src/app/_layout.tsx:494-546`).
- Focus mode still renders an explicit exit-focus X in the tabs row (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:368-394,1335-1339`), is not URL-selected, and compact/mobile outer chrome is not fully suppressed by the desktop mount condition. Therefore it should be reused as an internal projection primitive, not exposed as the embed contract. **[INFERENCE]** A separate embed flag should force the same pane projection while suppressing the exit control and all chrome affordances.

### Self-hosted browser endpoint resolution

- Self-hosted mode is enabled only when `EXPO_PUBLIC_PASEO_SELFHOSTED === "true"` (`packages/app/src/fork-features/self-hosted/runtime.ts:40-47`).
- The local-daemon override parser first checks a URL hash of the form `#tcp=<host>:<port>`, then `EXPO_PUBLIC_LOCAL_DAEMON`; the special value `self-hosted` resolves to the current page host (`packages/app/src/fork-features/self-hosted/runtime.ts:49-76`). Do not encode embed state in the hash: `#tcp=...` already has a fork-defined meaning. Use `?embed=1`.
- Self-hosted browser targeting derives endpoint, TLS, and port from `window.location` (`packages/app/src/fork-features/self-hosted/runtime.ts:91-107`). Multi-daemon mode fetches `/_paseo/hosts.json` and builds managed direct-TCP connections with optional `basePath` (`packages/app/src/fork-features/self-hosted/runtime.ts:78-88,110-132`).
- The Docker documentation confirms the intended browser behavior: the bundled UI listens on the web origin, and `EXPO_PUBLIC_LOCAL_DAEMON=self-hosted` lets the browser use the page host as the direct TCP endpoint (`docs/docker.md:7-21`). The same-origin multi-daemon manifest uses `/_paseo/hosts.json` plus per-daemon proxy paths such as `/daemons/alpha/` (`docs/docker.md:124-137`).

### First-load host bootstrap race to account for

- `HostRuntimeStore.runBoot` reads the override, loads stored profiles, and marks the host registry loaded before starting self-hosted manifest bootstrap; for a non-self-hosted override it launches `bootstrapConfiguredOverride` without awaiting it (`packages/app/src/runtime/host-runtime.ts:1531-1568`).
- `loadFromStorage` marks `hostRegistryStatus = "ready"` in its `finally` block (`packages/app/src/runtime/host-runtime.ts:1571-1617`). Self-hosted manifest reconciliation and probes happen afterward (`packages/app/src/runtime/host-runtime.ts:1647-1708`); configured override probing also happens asynchronously and retries until it can connect (`packages/app/src/runtime/host-runtime.ts:1767-1810`).
- A successful probe obtains the daemon-returned `serverId` and upserts the host profile (`packages/app/src/runtime/host-runtime.ts:1942-1971`).
- Once registry status is ready, a host route renders only if its `serverId` already exists; otherwise it redirects to the first host's open-project route or welcome (`packages/app/src/navigation/host-runtime-bootstrap.ts:217-252`). **[INFERENCE / BLOCKER]** A first-load shared URL containing a server ID that is not already stored can redirect before an async override/manifest probe inserts the profile. A reliable embed deployment must either pre-seed the host profile, use a route/host ID known to be present, or make startup hold the requested host route while configured/self-hosted bootstrap is still in flight. This is a host-runtime/navigation integration concern, not a daemon protocol change.

## 3. Recommended URLs and host assumptions

### Preferred known-workspace URL

```text
https://<ui-origin>/h/<serverId>/workspace/<workspaceId>?open=agent%3A<agentId>&embed=1
```

This uses the existing workspace open-intent contract (`packages/app/src/utils/host-routes.ts:122-127,362-373`) and preserves unrelated `embed=1` search state through the route-echo cleanup (`packages/app/src/utils/host-routes.ts:312-347`). The host ID must match a profile in the runtime registry or a bootstrap path that is guaranteed to finish before route resolution (`packages/app/src/navigation/host-runtime-bootstrap.ts:217-252`).

### Generic agent deep link

```text
https://<ui-origin>/h/<serverId>/agent/<agentId>?embed=1
```

This is an officially supported route shape (`packages/app/src/utils/host-routes.ts:267-287`), but the route resolves the workspace and then navigates through builders that do not preserve arbitrary query parameters (`packages/app/src/app/h/[serverId]/agent/[agentId].tsx:94-113`, `packages/app/src/stores/navigation-active-workspace-store/navigation.ts:117-126`). Treat `embed=1` preservation as a required small route patch if this URL must be the public contract.

### Direct TCP fragment when needed

```text
https://<ui-origin>/h/<serverId>/workspace/<workspaceId>?open=agent%3A<agentId>&embed=1#tcp=<daemon-host>:6767
```

The `#tcp=` fragment is the existing self-hosted direct-daemon override (`packages/app/src/fork-features/self-hosted/runtime.ts:54-75`). If the UI is served by the same reverse proxy as the daemon and `EXPO_PUBLIC_LOCAL_DAEMON=self-hosted` is configured, the page host can be used instead (`packages/app/src/fork-features/self-hosted/runtime.ts:68-74`; `docs/docker.md:7-21`).

## 4. Escape-control inventory

The table distinguishes controls that are visible in the target layout from controls that can remain reachable through an overlay, shortcut, or callback if the patch only hides one button.

| Escape / control | Evidence | Embed implication |
|---|---|---|
| New workspace, History, Search, Schedules | `SidebarNewWorkspaceRow`, history, search, and schedules rows route to `/new`, sessions, command center, and schedules (`packages/app/src/components/sidebar/sidebar-nav-rows.tsx:65-169`). | Hide the entire `LeftSidebar` and compact sidebar host; do not merely hide one row. |
| Workspace/project list and project settings | Workspace list routes to project settings (`packages/app/src/components/sidebar-workspace-list.tsx:546-548`) and contains project/new-workspace rows (`packages/app/src/components/sidebar-workspace-list.tsx:797-803,884-890`). | Suppress outer workspace-list rendering in embed. |
| Add project, host picker/list, import, global settings | `SidebarFooter` renders add-project, host picker, import, and settings actions (`packages/app/src/components/left-sidebar.tsx:444-503`). | Suppress outer footer with the sidebar; retain host runtime providers underneath. |
| Sidebar menu toggle | `SidebarMenuToggleButton` toggles desktop/mobile agent-list panel; `SidebarMenuToggle` conditionally returns the button (`packages/app/src/components/headers/menu-header.tsx:47-141`). | Hide/gate this toggle in workspace headers; otherwise it reopens the outer sidebar. |
| Workspace header / Git / editor / explorer actions | Workspace screen builds header-right actions containing scripts, editor, `WorkspaceActions`, and explorer toggles (`packages/app/src/screens/workspace/workspace-screen.tsx:3757-3804`); the desktop header includes `SidebarMenuToggle` (`packages/app/src/screens/workspace/workspace-screen.tsx:3879-3918`). | Do not render the workspace screen header in embed; keep the tabs row and center panel. |
| Mobile workspace menu, new agent/browser/terminal, terminal profile settings | Mobile workspace menu includes new agent/browser, workspace actions, new terminal/profile, and an edit-terminal-profiles route (`packages/app/src/screens/workspace/workspace-header-menu.tsx:202-300`). | Hiding the header/menu removes the visual path. Also suppress profile/settings callbacks in composer controls for defense in depth. |
| New tab, split right/down, maximize, exit focus | The tabs row contains inline plus and pane toolbar actions (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:1314-1392`); the plus control opens `WorkspaceNewTabMenuContent` (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:217-253`). The exit-focus X is rendered when focus is enabled (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:1335-1339`). | Keep existing tabs but set new-tab/split/maximize actions false and hide exit-focus in embed. Consider forcing the focused pane projection. |
| Explorer sidebar/dock | `SplitContainer` separately renders the explorer dock when not focused (`packages/app/src/components/split-container.tsx:442-444,705-738`); workspace screen supplies explorer header toggles (`packages/app/src/screens/workspace/workspace-screen.tsx:3825-3847`). | Force the embed projection to suppress explorer dock and its toggle. |
| Agent profile editor/settings escape | Agent controls construct an edit-profile navigation callback and pass profile create/edit handlers to `CombinedModelSelector` (`packages/app/src/composer/agent-controls/index.tsx:1646-1653,1780-1794`). Draft controls do the same (`packages/app/src/composer/agent-controls/index.tsx:1868-1910`). The model selector itself receives profile handlers (`packages/app/src/composer/agent-controls/index.tsx:983-1007`). | Keep provider/model/thinking/features controls, but pass null/no-op profile edit/create handlers in embed. Do not hide all `AgentControls`. |
| Permission actions | Permission cards and allow/deny actions are part of the agent stream (`packages/app/src/agent-stream/view.tsx:1383-1513`). | Preserve these; they are required interaction, not navigation escape. |
| Global command center, host chooser, provider settings, setup/add-project hosts | `AppContainer` mounts command-center actions/root, add-project flow, host chooser, provider settings, and setup dialog hosts (`packages/app/src/app/_layout.tsx:584-623`). | Audit keyboard shortcuts and global overlays in embed. At minimum, prevent embed-only visual launchers; if command-center shortcuts can open route/navigation actions, gate their registrations or action handlers while embed is active. |
| Launcher terminal profile settings | Launcher has an `editTerminalProfiles` action that navigates to host terminal settings (`packages/app/src/workspace-tabs/launcher/index.tsx:96-118,227-249`). | Hiding the new-tab launcher is sufficient for the visual path; gate the action as defense in depth if launchers can be opened by shortcut or persisted tab state. |

## 5. Minimal patch shape

The fork already records self-hosted runtime as fork-owned and trunk integration edits separately (`fork-features/ownership.tsv:1-8`; `fork-features/trunk-patches.md:1-23,33-43`). Keep embed-specific implementation in `fork-features/`, with only the smallest route/layout wiring in existing app files.

### Fork-owned helper

Add a pure helper at `packages/app/src/fork-features/embed/runtime.ts` (new file; recommended ownership entry in `fork-features/ownership.tsv`). It should parse one explicit query flag, e.g. `embed=1`, from a provided search string and return a named boolean/value type. Do not use the hash because `#tcp=<host>:<port>` is already a self-hosted connection contract (`packages/app/src/fork-features/self-hosted/runtime.ts:54-75`). Keep the helper free of React/router/IO so route and component code can share it.

### Trunk integration edits (recommended order)

1. **Outer shell (`src/app/_layout.tsx`)**: derive `embedMode` from the current URL and make the outer `LeftSidebar`, `CompactExplorerSidebarHost`, workspace gesture/panel affordances, and desktop sidebar visibility conditional on `!embedMode`; retain host runtime/provider mounts. Existing `chromeEnabled` is the narrowest desktop entry point (`packages/app/src/app/_layout.tsx:461-578`; `packages/app/src/components/desktop-sidebar-layout.ts:6-20`). Do not delete providers or globally remove `CommandCenter`/host runtime.
2. **Workspace shell (`src/screens/workspace/workspace-screen.tsx`)**: pass embed state into the desktop split path; omit `renderWorkspaceScreenHeader`, header-right actions, `SidebarMenuToggle`, explorer toggles, and any mobile workspace menu in embed (`packages/app/src/screens/workspace/workspace-screen.tsx:3757-3804,3879-3918,3950-3989`). Keep the tabs row and `WorkspacePanelContent` (`packages/app/src/screens/workspace/workspace-screen.tsx:4036-4093`).
3. **Split projection (`src/components/split-container.tsx`)**: add a small `embedMode` prop or use an explicitly named `projectFocusedPane` condition. Suppress explorer dock and route/embed pane state through the existing focus-root projection (`packages/app/src/components/split-container.tsx:374-444`; `packages/app/src/components/split-container-focus.ts:3-15`). Do not rely on persisted focus state alone, because embed must be deterministic from the URL.
4. **Tabs (`src/screens/workspace/workspace-desktop-tabs-row.tsx`)**: add an embed presentation prop and force `showPaneSplitActions`, `showPaneMaximizeAction`, and new-tab presentation false; suppress `WorkspaceExitFocusModeButton` in embed while retaining tab navigation/close behavior (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:500-531,1314-1396`). If persisted state has multiple panes, use the focused-pane projection rather than exposing the split controls.
5. **Agent controls (`src/composer/agent-controls/index.tsx`)**: preserve provider/model/thinking/effort/features controls (their selection props and test IDs are already isolated), but suppress only profile editor/create/edit callbacks while embed is active (`packages/app/src/composer/agent-controls/index.tsx:520-569,891-1104,1646-1653,1780-1794,1868-1910`). This keeps the required model/effort control surface without a settings escape.
6. **Agent deep-link query preservation**: either document the known-workspace URL as the only public embed URL, or extend the agent route/navigation input to carry `embed=1` through `navigateToAgent`/`navigateToWorkspace`. The current generic route redirect is the exact place where an arbitrary query can be dropped (`packages/app/src/app/h/[serverId]/agent/[agentId].tsx:94-113`; `packages/app/src/utils/navigate-to-agent/resolve.ts:24-44`; `packages/app/src/stores/navigation-active-workspace-store/navigation.ts:117-126`).
7. **Host startup behavior**: do not alter daemon/protocol code for the UI-only embed patch. If first-load shared links must work without a pre-existing host profile, add a narrowly scoped startup hold/route-aware wait so unknown host routes are not redirected until configured override or self-hosted manifest bootstrap has had a chance to upsert the profile. The current ordering and redirect are documented above (`packages/app/src/runtime/host-runtime.ts:1531-1568,1607-1617`; `packages/app/src/navigation/host-runtime-bootstrap.ts:217-252`). This is a required deployment decision, not something the embed CSS/layout patch can solve.

### Approximate change size

**[INFERENCE]** If the known-workspace URL is the contract and no host-startup change is required, the UI patch is likely one new pure helper plus small conditional plumbing in `_layout.tsx`, `workspace-screen.tsx`, `split-container.tsx`, `workspace-desktop-tabs-row.tsx`, and `agent-controls/index.tsx` (roughly 5 existing files, with no new state store). Generic agent URL preservation and first-load host waiting would add separate route/runtime edits and should be treated as explicit acceptance criteria rather than hidden follow-up work.

## 6. Runtime verification plan for the implementation worker

The repository documents the local web runtime as an Expo app on port 8081 with the dev daemon on port 6768: `npm run dev:app` sets `PASEO_LISTEN=127.0.0.1:6768 EXPO_PORT=8081` (`/Users/mouriya/Ext/code/paseo/package.json:36-42`), and development docs describe `http://localhost:8081` plus daemon 6768 (`docs/development.md:8-20,63-71`). The app package's web script uses Expo web (`packages/app/package.json:6-39`), and Playwright's base URL defaults to `http://localhost:${E2E_METRO_PORT ?? "8081"}` (`packages/app/playwright.config.ts:3-6`).

After implementation, verify through the real web UI (not only a unit test):

1. Start the documented dev daemon/app and open a known-workspace URL with `?open=agent%3A...&embed=1` (or the public generic agent URL if query preservation was implemented) (`docs/development.md:8-20`; `/Users/mouriya/Ext/code/paseo/package.json:36-42`).
2. Confirm the main pane is present: `workspace-tabs-row`, agent conversation/stream, composer, `agent-provider-selector`, and `agent-thinking-selector`/feature control when available (`packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:1314-1345`; `packages/app/src/composer/agent-controls/index.tsx:956-1104`).
3. Confirm outer escape selectors/actions are absent or inert: sidebar new workspace/history/search/schedules, add project/settings/host picker, workspace header/sidebar toggle, explorer dock/toggle, new-tab/split/maximize, and profile editor route (`packages/app/src/components/sidebar/sidebar-nav-rows.tsx:65-169`; `packages/app/src/components/left-sidebar.tsx:444-503`; `packages/app/src/components/headers/menu-header.tsx:47-141`; `packages/app/src/screens/workspace/workspace-desktop-tabs-row.tsx:1314-1392`; `packages/app/src/composer/agent-controls/index.tsx:1646-1653`).
4. In the live conversation, change model and thinking/effort, submit a prompt, and exercise an actual pending permission allow/deny action; verify the agent stream/composer still function (`packages/app/src/composer/agent-controls/index.tsx:983-1056`; `packages/app/src/agent-stream/view.tsx:1383-1513`).
5. Refresh the same embed URL and use browser back/forward. Confirm `embed=1` remains effective and `open` is consumed only after the workspace is hydrated (`packages/app/src/app/h/[serverId]/workspace/[workspaceId]/index.tsx:114-194`; `packages/app/src/utils/host-routes.ts:312-347`).
6. Test a first-load self-hosted URL with the intended `EXPO_PUBLIC_LOCAL_DAEMON=self-hosted`, `#tcp=...`, or `/_paseo/hosts.json` deployment, and explicitly observe whether host bootstrap completes before the host route redirects (`packages/app/src/fork-features/self-hosted/runtime.ts:54-107`; `packages/app/src/runtime/host-runtime.ts:1531-1568,1647-1708`; `packages/app/src/navigation/host-runtime-bootstrap.ts:217-252`).

This verification was not run here because the assignment is read-only investigation and prohibits builds/tests/runtime edits. 
