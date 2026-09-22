# Implementation slices (Phase A) — internal contract

Contract source: /Users/mouriya/Ext/code/alicedev/ARCHITECTURE.md (read fully). Plan: /Users/mouriya/.claude/plans/cheerful-drifting-wind.md.
Shared fixed types (do NOT change without the hub): bot/alicedev/dsl/model.py, bot/alicedev/domain.py.
Branch: feat/upgrade-bot-loop in /Users/mouriya/Ext/code/alicedev. Each slice commits ONLY its own files (git add <paths>), never `git add -A`, never pushes. Zero production changes (no ssh writes to nekoringo2, no docker on prod). Python: target 3.12 (AstrBot runtime), local test with `uv run --with pytest --with pyyaml --with jinja2 --with duckdb==1.5.5 --with aiohttp --with python-frontmatter pytest bot/tests -q` from repo root (tests prepend bot/ to sys.path).

## Slice DSL (owns)
- bot/alicedev/dsl/** except model.py: loader.py (`load_registry(root: Path) -> Registry`, per-file rejection with DslError(path,line,msg), never raises for content errors), usage.py (parse usage line -> UsageParam tuple), invocation.py, render.py, reply_instructions.py, __main__.py (`python -m alicedev.dsl check <root>` exit!=0 on errors; `card <name> --out <png>` via render/cli.py T2IHttpHost style, t2i endpoint arg).
- Public API consumed by runtime:
  - `render(t: Tmpl, ctx: Mapping) -> str` (jinja2 StrictUndefined, autoescape False, trim_blocks, lstrip_blocks).
  - `match_command(reg: Registry, text: str) -> Invocation | None`: text starts with `/` or `／`; resolves name/alias, then optional subcommand word, then optional session marker `%n`/`％n` (with or without space, right after name or subcommand word); returns Invocation(command: Command, session_no: int|None, rest: str). None if not a known command.
  - `parse_args(cmd: Command, inv: Invocation) -> ParsedArgs | ArgError`: fills per UsageParam: session -> int|None (from inv.session_no), text -> rest string, word/int/page/github_ref raw; mentions/quoted are marked as "from event" (runtime fills). ArgError(message) for missing required.
  - `reply_instructions(scenario: Scenario, state: AgentState) -> str`: the 「回复方式」 appendix per §3.4 (reply spec, next targets with required data and visibility, chat_reply usage incl. transition, long content -> image_template).
  - `message_key(action_type, result) -> str` = e.g. "start.created"; messages.yaml must define every action.result plus system keys: unknown_command, permission_denied(not sent, log only), usage_error, internal_error, agent_create_failed, main_sync_failed, state_entered.<state> optional.
- bot/alicedev/render/views.py: pure view-model builders -> (card_name, fields dict): help_card(reg, current: SessionView|None), command_card(cmd, scenario|None), session_card(view, scenario), session_list_card(rows, page, pages, archived), favorites_list_card(...), requirements_list_card(...). Scenario flow = states+transitions as plain HTML-friendly data (no mermaid).
- templates/** : commands/*.yaml (15 files per ARCHITECTURE §9), routes.yaml, messages.yaml, scenarios/{requirement,investigate,github-issue,github-pr,upgrade-bot}/scenario.yaml + prompts (migrate old templates/prompts/*.md bodies; upgrade-bot working.md/deploying.md per §13.2), cards/{help,command,session,session_list}.html (+ keep/adjust generic_card, requirement_summary, requirements_list, favorites_list); delete templates/prompts/, cards/upgrade_*.html.
- tests: bot/tests/test_dsl_*.py (loader errors incl. unquoted-# truncation caught by examples check, usage parse, % and ％, subcommands, render strict, real templates/ load with zero errors).

## Slice RUNTIME (owns)
- Everything else under bot/: store (schema v3 + migration from current prod schema, see plan design 6), paseo/ (CLI PaseoControl via `docker exec alicedev-paseo paseo … --json`; container name configurable; delete mcp.py/daemon_ws.py), scheduler/, outbox/, actions/ (execute Action variants, arg resolution: session default quoted->current; permission check; say/messages rendering via dsl.render), api/ (/v1/reply 202 rules §6, /v1/agents/{agent}, /v1/status incl dsl_errors+revision, /v1/health {generation, revision} where revision = contents of bot/REVISION file or "unknown"), main.py wiring (entry `@filter.event_message_type(ALL)`), config (_conf_schema.json drop paseo_* ; add paseo_container default "alicedev-paseo", paseo_bin default "paseo", docker_bin default "docker"), mainsync invocation (`docker exec alicedev-paseo git …` semantics of tools/mainsync), workspace_register, reports (session_id), images, github reuse, gateway_client (share target §8, no embed), delete upgrade/, commands/ (keep reusable parsing helpers by moving to actions/), templates/registry.py.
- Consumes DSL API above. Until DSL slice lands, code against the signatures; integrate at the end.
- tests: store migration (build an old-schema DB fixture from current schema.sql + sample rows, migrate, assert), scheduler transitions & enqueue rules (fake PaseoControl), outbox ordering/retry, paseoctl JSON parsing, arg/session resolution.

## Slice HARNESS (owns harness/**)
Per plan A1. Wire field `agent`, `--agent`; chat_reply `{reply?, transition?}`; image kind; reply-cli 202 + queued|replayed; pending persists text; session_stop also checks ctx closing. `make harness` + typecheck.

## Slice GATEWAY (owns gateway/**)
Per plan A4 + ARCHITECTURE §8. Delete gateway/build/. Add tests (pytest + aiohttp test utils).

## Slice INFRA (owns tools/**, deploy/**, Makefile, nekoringo-iac repo edits (no apply, no push))
Per plan A5, A6, A8 and design 1-4,7. Local lint/py_compile + shellcheck where available; no prod.
