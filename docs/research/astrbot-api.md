# AstrBot plugin/API facts for alicedev

## Scope and version pin

This document is source-backed research for alicedev; it does not implement the plugin. The AstrBot source inspected is **4.28.1** (`pyproject.toml` and `astrbot/__init__.py`) at commit [`f99c76ec106f9d47a909393e463197687eec4070`](https://github.com/AstrBotDevs/AstrBot/tree/f99c76ec106f9d47a909393e463197687eec4070). The current source is the authority where an older guide/template differs.

The official template inspected is [`Soulter/helloworld`](https://github.com/Soulter/helloworld), default branch `master`; its `metadata.yaml` and `main.py` are cited below. The HTML renderer service inspected is [`AstrBotDevs/astrbot-t2i-service`](https://github.com/AstrBotDevs/astrbot-t2i-service), commit [`bcc08e47274c69e5ace19bbf738b95260ce17ca7`](https://github.com/AstrBotDevs/astrbot-t2i-service/tree/bcc08e47274c69e5ace19bbf738b95260ce17ca7).

## 1. Plugin skeleton and message API

### 1.1 Files, loading, metadata, and registration

A plugin directory is normally placed at `AstrBot/data/plugins/<plugin-directory>`, with `metadata.yaml` and `main.py`; the official guide's clone example creates `AstrBot/data/plugins` and clones the plugin there ([plugin-new.md](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/plugin-new.md)). The loader scans `data/plugins` for a directory containing `main.py` (or a same-name Python module) ([`star_manager.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/star_manager.py)).

The template's metadata is:

```yaml
name: helloworld
# display_name is supported by current AstrBot (>=4.5.0)
display_name: helloworld
desc: AstrBot 插件示例。
version: v1.3.0
author: Soulter
repo: https://github.com/Soulter/helloworld
```

Source: [`Soulter/helloworld/metadata.yaml`](https://github.com/Soulter/helloworld/blob/master/metadata.yaml). Current plugin docs additionally describe optional `support_platforms` and `astrbot_version` fields; the version range is PEP 440 and must not have a `v` prefix ([`plugin-new.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/plugin-new.md)).

A plugin class subclasses `Star`, receives `Context`, and may implement `initialize()` and `terminate()`. Current `Star` auto-registers subclasses in `__init_subclass__` ([`base.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/base.py)). The public `register` decorator is still exported, and the template uses it, but current source labels the underlying `register_star` decorator **deprecated** after v3.5.19: inheriting from `Star` is sufficient ([`star.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/register/star.py)). For compatibility with the requested sample, `@register(...)` is shown below; a new plugin should normally rely on `metadata.yaml` plus the `Star` subclass and omit the deprecated decorator.

### 1.2 Commands, regex, event types, and arguments

The public API exports command and regex registration from `astrbot.api`/`astrbot.api.event.filter` ([`astrbot/api/all.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/api/all.py), [`star_handler.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/register/star_handler.py)):

```python
from astrbot.api.event import AstrMessageEvent, filter

@filter.command("需求")
async def demand(self, event: AstrMessageEvent, text: str):
    # AstrBot parses typed command arguments; `/需求 hello world` supplies text.
    yield event.plain_result(text)

@filter.regex(r"^需求\\s+(.+)$")
async def demand_regex(self, event: AstrMessageEvent):
    # RegexFilter only tests re.search(event.get_message_str().strip()).
    # Extract groups yourself with re.search against event.message_str.
    ...
```

`@filter.command` handlers may be async generators that `yield` results, or may send with `await event.send(...)`. The event handler registration source creates an `AdapterMessageEvent` handler and adds a `CommandFilter` or `RegexFilter` ([`star_handler.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/register/star_handler.py)). `RegexFilter` itself does not inject a match object into the handler; it only returns whether `regex.search(event.get_message_str().strip())` succeeds ([`regex.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/filter/regex.py)).

Command groups use `@filter.command_group("math")`, then `@math.command("add")`; nested groups use `@math.group("calc")` ([`listen-message-event.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/listen-message-event.md)). Command names cannot contain spaces. Aliases use `alias={...}`. Multiple filters are ANDed.

The current event message enum is `GROUP_MESSAGE`, `PRIVATE_MESSAGE`, `OTHER_MESSAGE`, and `ALL` ([`event_message_type.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/filter/event_message_type.py)). Platform filters include `AIOCQHTTP`, `QQOFFICIAL`, `QQOFFICIAL_WEBHOOK`, `TELEGRAM`, and other adapter flags; the adapter name-to-flag map is in [`platform_adapter_type.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/filter/platform_adapter_type.py).

### 1.3 Reading text, IDs, platform, quoted messages, and mentions

`AstrMessageEvent` and `AstrBotMessage` expose the following stable accessors/fields ([`astr_message_event.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070), [`astrbot_message.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070)):

| Need | API/fact |
|---|---|
| Plain text/raw parsed text | `event.message_str` or `event.get_message_str()`; this is the concatenation of `Plain` components. |
| Full incoming chain | `event.get_messages()` or `event.message_obj.message`; use `event.message_obj.raw_message` for the platform-native object. |
| Typed command arguments | Add typed parameters after `event` (`text: str`, `a: int`, etc.); command parsing supplies them. For unstructured remainder, read `event.message_str`. |
| Sender ID/name | `event.get_sender_id()` and `event.get_sender_name()`; underlying `message_obj.sender.user_id` / `.nickname` are also available. |
| Group ID | `event.get_group_id()` or `event.message_obj.group_id`; private chats return `""`. |
| Platform | `event.get_platform_name()` (adapter type such as `telegram`, `aiocqhttp`) and `event.get_platform_id()` (instance ID). |
| Message ID | `event.message_obj.message_id`. |
| Session ID | `event.unified_msg_origin`, `event.session_id`, or `event.get_session_id()`. |
| Quoted/replied inbound message | A `Comp.Reply` component in `event.get_messages()`; it carries `id`, optional `chain`, sender fields, timestamp, and `message_str`. |
| @ mention | A `Comp.At` component; fields are `qq` (target ID, or `"all"`) and optional `name`. |
| Incoming image/file | Iterate the chain and inspect `Comp.Image`, `Comp.File`, etc.; `Image.file`/`url` are references, and `await image.convert_to_file_path()` resolves a local path. |

`AstrBotMessage` also stores `type`, `self_id`, `session_id`, `message_id`, `group`, `sender`, `message`, `message_str`, `raw_message`, and `timestamp`. `Group` contains `group_id` and optional `group_name` ([`astrbot_message.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/astrbot_message.py)).

Incoming adapter details matter:

- Telegram recursively converts `reply_to_message` into `Comp.Reply`; text entities of type `mention` become `Comp.At(qq=username, name=username)`, and a mention of the bot is removed from the plain command text ([`tg_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/telegram/tg_adapter.py)). Telegram photos become `Comp.Image(file=file.file_path, url=file.file_path)`, and documents become `Comp.File`; captions are added as `Plain` ([same source](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/telegram/tg_adapter.py)).
- OneBot/aiocqhttp resolves a received `reply` through `get_msg`, constructs `Reply` with the quoted chain, and resolves `at` user information into `At(qq=..., name=...)` ([`aiocqhttp_platform_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)).

### 1.4 Building and sending text/images/files/mentions/replies

Public result helpers are implemented on `AstrMessageEvent` ([`astr_message_event.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070)):

```python
yield event.plain_result("text")
yield event.image_result("/path/to/image.png")       # local path
# image_result("https://...") also chooses a URL image
yield event.chain_result([
    Comp.At(qq=sender_id, name=sender_name),
    Comp.Plain("hello"),
    Comp.Image.fromFileSystem("/path/to/image.png"),
    Comp.Reply(id=event.message_obj.message_id),
])
# Or: await event.send(MessageChain([...]))
```

`event.chain_result(chain)` creates a `MessageEventResult` whose chain is the supplied list. `event.send(message_chain)` sends immediately. `Comp.Plain(text)` creates text; `Comp.At(qq=...)` creates a mention; `Comp.Reply(id=...)` creates a reply segment. `Reply` also accepts `chain`, `sender_id`, `sender_nickname`, `time`, and `message_str` when the caller has the quoted message details ([`components.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/message/components.py)).

`Comp.Image` constructors are:

- `Image.fromBytes(byte: bytes)` → base64-backed image component.
- `Image.fromIO(io_obj)` → reads bytes and delegates to `fromBytes`.
- `Image.fromFileSystem(path)` → file URI plus local path.
- `Image.fromURL(url)` → HTTP(S) URL.
- `Image.fromBase64(base64)` → base64-backed component.

`Image.convert_to_file_path()` resolves URL/base64/path through the media resolver; this is what adapters use before upload ([`components.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/message/components.py)). Files use `Comp.File(name=..., file=path, url=url)` and `await file.get_file()` ([same source](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/message/components.py)).

**Telegram-specific facts:** The adapter supports receive/send Text, Image, Voice, Video, and File, and proactive push according to the official platform guide ([`telegram.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/platform/telegram.md)). Its send path uses `reply_to_message_id` when a `Reply` is present, uploads local images with `send_photo` (GIFs with `send_animation`), and uploads files with `send_document` ([`tg_event.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/telegram/tg_event.py)). Thus Telegram quote/reply and image/file sending are source-backed.

Telegram `At` is **not numeric-ID addressing** in the current sender implementation: `send_with_client` takes `i.name` and prefixes the next `Plain` as textual `@<name>`; the numeric `i.qq` is not passed to a Telegram Bot API mention entity ([`tg_event.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/telegram/tg_event.py)). Incoming Telegram mentions likewise use the username as `At.qq`/`At.name`. Therefore, for Telegram, pass a username in `At.name` when available; `At(qq=numeric_id)` alone is not proven to mention that user. [UNKNOWN: test the desired Telegram mention UX with a live bot, especially users without a public username; source only proves the current textual-username path.]

### 1.5 Minimal source-matched plugin sample

This deliberately uses the requested `@register` compatibility decorator, although current source says it is deprecated. It renders custom HTML through `Star.html_render`, returns a local image path, and sends a quoted image plus an `At` component:

```python
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.api.star import Context, Star, register


CARD = """
<!doctype html>
<html>
  <head>
    <meta name="viewport" content="width=800">
    <style>
      body { width: 760px; padding: 24px; font-family: sans-serif; }
      h1 { color: #2f86bd; }
    </style>
  </head>
  <body><h1>{{ title }}</h1><p>{{ body }}</p></body>
</html>
"""


@register("alicedev", "mouriya-s-lab", "paseo bridge", "0.1.0")
class AliceDev(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.command("需求")
    async def demand(self, event: AstrMessageEvent, text: str):
        image_path = await self.html_render(
            CARD,
            {"title": "需求", "body": text},
            return_url=False,
            options={"type": "png", "full_page": True},
        )
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
        incoming = event.message_obj
        yield event.chain_result(
            [
                Reply(
                    id=incoming.message_id,
                    sender_id=sender_id,
                    sender_nickname=sender_name,
                    message_str=incoming.message_str,
                ),
                At(qq=sender_id, name=sender_name),
                Plain(" 已生成需求卡片"),
                Image.fromFileSystem(image_path),
            ]
        )
```

This sample is source-verified, not runtime-executed in this investigation. The `text: str` parameter depends on AstrBot's command argument parser; if the desired Chinese command/remainder parsing differs in the target runtime, test `/需求 ...` through Telegram. The current Telegram implementation's `At` behavior means `sender_name` must be a usable Telegram username for a real @ link.

### 1.6 Plugin configuration and persistence

A plugin `_conf_schema.json` declares dashboard-editable configuration. Supported schema types include `string`, `text`, `int`, `float`, `bool`, `object`, `list`, `dict`, `template_list`, and (v4.13.0+) `file`; `object` uses nested `items`, and `secret: true` masks a value in the UI but does not encrypt it ([`plugin-config.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/plugin-config.md)). Example:

```json
{
  "paseo_socket": {
    "type": "string",
    "description": "Local paseo Unix socket",
    "default": "/run/paseo.sock"
  },
  "allowed_groups": {
    "type": "list",
    "items": {"type": "string"},
    "default": []
  },
  "token": {
    "type": "string",
    "secret": true,
    "default": ""
  }
}
```

When a plugin has `_conf_schema.json`, AstrBot creates/updates `data/config/<plugin_name>_config.json` and passes an `AstrBotConfig` to `__init__`. `AstrBotConfig` is dict-like; `self.config.get(...)`, indexing, and `self.config.save_config()` are available ([`plugin-config.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/plugin-config.md), [`astrbot_config.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/config/astrbot_config.py)). The current `Star` constructor accepts `context` and optional `config`; use the documented constructor form with `config: AstrBotConfig` when a schema is present.

For persistence, the `Star` base includes `PluginKVStoreMixin`: `await self.put_kv_data(key, value)`, `await self.get_kv_data(key, default)`, and `await self.delete_kv_data(key)`; supported values are scalar/bytes/dict/list/None and are isolated per plugin ([`plugin_kv_store.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/plugin_kv_store.py), [`storage.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/storage.md)). For larger files, use `data/plugin_data/{plugin_name}/`; `get_astrbot_plugin_data_path()` resolves the root `data/plugin_data` directory ([`astrbot_path.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/astrbot_path.py)). Do not store durable data in the plugin source directory because updates replace it ([`plugin-new.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/plugin-new.md)).

For background work, the maintained plugin guide explicitly shows `asyncio.create_task(self.my_task())` in `__init__` and an async task loop ([older maintained guide section](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/zh/dev/star/plugin.md)). A production plugin should retain the task, cancel/await it in `terminate()`, and handle `asyncio.CancelledError`; task retention/cancellation is an engineering recommendation, not an additional AstrBot decorator contract.

## 2. HTML-to-image / text-to-image

### 2.1 Public methods and output modes

`Star` exposes:

```python
async def text_to_image(self, text: str, return_url=True) -> str
async def html_render(
    self,
    tmpl: str,
    data: dict,
    return_url=True,
    options: dict | None = None,
) -> str
```

Source: [`astrbot/core/star/base.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/base.py). `text_to_image` delegates to `html_renderer.render_t2i`; `html_render` delegates to `render_custom_template` ([`astrbot/core/utils/t2i/renderer.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/t2i/renderer.py)).

The custom template is HTML with Jinja2 syntax. The official guide's example passes `tmpl` and a data dictionary, uses a Jinja `{% for %}` loop, and returns the result to `event.image_result(...)` ([`html-to-pic.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/html-to-pic.md)). Inline `<style>` CSS works because the renderer screenshots a browser page. The service accepts `tmpl`, `tmpldata`, or raw `html`; its README documents the same Jinja2 and screenshot options ([T2I service README](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/README.md)).

- `return_url=True`: AstrBot posts JSON with `json: true`; the network service responds with an image ID and AstrBot returns an endpoint URL of the form `<endpoint>/<id>` ([`network_strategy.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/t2i/network_strategy.py)).
- `return_url=False`: AstrBot posts/generates and downloads the result to a local temporary image path, which is suitable for `Image.fromFileSystem` or `event.image_result` ([same source](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070)).
- Network custom HTML defaults to `full_page=True`, JPEG, quality 40; pass Playwright screenshot options through `options`, such as `type`, `quality`, `omit_background`, `full_page`, `clip`, `animations`, `caret`, `scale`, `viewport_width`, `viewport_height`, and `device_scale_factor_level` ([`network_strategy.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070), [T2I `ScreenshotOptions`](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/src/render.py)).

For CSS/images, put CSS inline or in HTML. The T2I service writes rendered HTML to a temporary file and navigates Playwright to `file://...`; remote HTTP(S) images and data URIs are natural options. [UNKNOWN: external asset reachability and relative local asset paths from an AstrBot container/T2I sidecar should be tested; a sidecar cannot see plugin-local files unless they are mounted or embedded/data-URI encoded.]

### 2.2 Renderer implementation and the “local” distinction

AstrBot's `HtmlRenderer` has `NetworkRenderStrategy` and `LocalRenderStrategy` ([`renderer.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/t2i/renderer.py)). The network strategy calls the default AstrBot T2I endpoint or configured `t2i_endpoint`; it is used for `html_render` and normal network `text_to_image` ([`network_strategy.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070)). The official T2I service uses Jinja2 `SandboxedEnvironment`, Playwright Chromium, and local temporary HTML/image files ([`src/render.py`](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/src/render.py), [`src/api.py`](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/src/api.py)). Its Dockerfile installs Chromium with `playwright install --with-deps chromium` ([T2I Dockerfile](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/Dockerfile)).

The built-in `LocalRenderStrategy` is **Pillow Markdown rendering**, not local HTML/Playwright. It implements `render(text)` and explicitly raises `NotImplementedError` for `render_custom_template` ([`local_strategy.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/t2i/local_strategy.py)). Therefore:

- To render ordinary Markdown/text locally inside the AstrBot Docker container, set `t2i_strategy` to `"local"` in `data/cmd_config.json`; optionally place `font.ttf` in `data/` for local font customization ([`default.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/config/default.py), [`astrbot-config.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/astrbot-config.md)).
- **There is no built-in local HTML renderer in AstrBot 4.28.1.** Setting `t2i_strategy=local` does not make `html_render` local; custom HTML goes through the network strategy.
- For local HTML rendering, run the official `soulter/astrbot-t2i-service:latest` sidecar (Playwright runs locally in that container) and set AstrBot's `t2i_strategy` to `remote` with `t2i_endpoint` pointing at `http://<service-name>:8999`. The official self-host guide gives `docker run -itd -p 8999:8999 soulter/astrbot-t2i-service:latest` and explains the endpoint setting ([`self-host-t2i.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/others/self-host-t2i.md)). This is local deployment, but it remains “remote/network strategy” from AstrBot's perspective.

[UNKNOWN: whether a future AstrBot release adds a local HTML backend; pin 4.28.1 for implementation and re-check on upgrade.]

## 3. Docker deployment and pre-provisioned configuration

### 3.1 Official image and volumes

The official Docker image is `soulter/astrbot:latest`. The documented standalone command publishes dashboard port `6185`, OneBot reverse WebSocket port `6199`, and mounts host `./data` to container `/AstrBot/data` ([Docker deployment guide](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/deploy/astrbot/docker.md)):

```bash
docker run -itd \
  -p 6185:6185 \
  -p 6199:6199 \
  -v "$PWD/data:/AstrBot/data" \
  -v /etc/localtime:/etc/localtime:ro \
  -v /etc/timezone:/etc/timezone:ro \
  --name astrbot \
  soulter/astrbot:latest
```

The official `compose.yml` likewise uses `soulter/astrbot:latest`, publishes `6185` and `6199`, sets `TZ=Asia/Shanghai`, and mounts `./data:/AstrBot/data` ([`compose.yml`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/compose.yml)). AstrBot's plugin root is `data/plugins` ([`astrbot_path.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/utils/astrbot_path.py)); there is no separate required official plugin volume because plugins live below the data volume. A separate host bind may be nested at `./plugins:/AstrBot/data/plugins`, but the parent `./data` mount is the documented path.

### 3.2 Installing a local plugin into the container

The source-based official workflow clones a plugin into `AstrBot/data/plugins/<plugin>` ([old plugin guide](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/zh/dev/star/plugin.md)). For a running Docker instance, the practical local-directory workflow is:

1. Copy or bind-mount the plugin directory to host `./data/plugins/alicedev/`.
2. Ensure it contains `metadata.yaml` and `main.py`.
3. Restart the container, or use WebUI `Extensions → Plugins → Reload Extension` after the directory is visible.

The loader's `_get_modules` scans each immediate plugin directory for `main.py` (or a same-name module) ([`star_manager.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/star_manager.py)). `requirements.txt`, if present, is installed by the plugin manager ([`plugin-new.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/plugin-new.md), [`star_manager.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/star_manager.py)). The CLI's `astrbot plug install` is marketplace/plugin-manager installation, not documented as a local-directory install; direct data/plugins bind/copy is the deterministic method for alicedev.

### 3.3 Telegram config and polling

Current Telegram platform object keys are shown in `DEFAULT_CONFIG`'s platform template ([`default.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070)):

```json
{
  "id": "telegram",
  "type": "telegram",
  "enable": true,
  "telegram_token": "<BotFather token>",
  "start_message": "Hello, I'm AstrBot!",
  "telegram_api_base_url": "https://api.telegram.org/bot",
  "telegram_file_base_url": "https://api.telegram.org/file/bot",
  "telegram_command_register": true,
  "telegram_command_auto_refresh": true,
  "telegram_command_register_interval": 300,
  "telegram_polling_restart_delay": 5.0
}
```

The adapter reads `self.config["telegram_token"]`, builds `python-telegram-bot`'s `Application`, and calls `updater.start_polling()`; the current adapter has no Telegram webhook configuration path ([`tg_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/telegram/tg_adapter.py)). The official Telegram guide instructs creating a BotFather token, disabling privacy mode for group-wide message access, selecting `telegram` in Platforms, and entering the Bot Token ([`telegram.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/platform/telegram.md)).

### 3.4 Dashboard port/auth and `cmd_config.json`

AstrBot reads `data/cmd_config.json` at startup; other WebUI profiles are under `data/config/` ([`astrbot-config.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/astrbot-config.md)). Current defaults include:

```json
{
  "dashboard": {
    "enable": true,
    "username": "astrbot",
    "password": "",
    "pbkdf2_password": "",
    "password_storage_upgraded": false,
    "password_change_required": false,
    "host": "0.0.0.0",
    "port": 6185
  },
  "platform": [],
  "t2i_strategy": "remote",
  "t2i_endpoint": "",
  "plugin_set": ["*"]
}
```

`AstrBotConfig` fills missing keys from `DEFAULT_CONFIG`, creates the file if absent, and generates password hashes when dashboard password fields are empty. It supports the environment variable `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` for deterministic first-run password provisioning ([`astrbot_config.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/config/astrbot_config.py)). The config must not contain a plaintext password in the `password` field; use the environment variable for first startup or `astrbot conf set dashboard.password ...` (the CLI writes hashes) ([`cmd_conf.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070), [`cli.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/use/cli.md)).

A minimal pre-provisioning shape can therefore be:

```json
{
  "dashboard": {
    "enable": true,
    "username": "astrbot",
    "host": "0.0.0.0",
    "port": 6185
  },
  "platform": [
    {
      "id": "telegram",
      "type": "telegram",
      "enable": true,
      "telegram_token": "${TELEGRAM_BOT_TOKEN}"
    }
  ],
  "t2i_strategy": "remote",
  "t2i_endpoint": "http://t2i:8999",
  "plugin_set": ["*"]
}
```

`${TELEGRAM_BOT_TOKEN}` is a deployment-templating placeholder, not a value AstrBot expands itself; render the secret into the file or use the host's secret-injection workflow. On first startup, set `ASTRBOT_DASHBOARD_INITIAL_PASSWORD` in the container environment rather than committing a token/password. AstrBot will add omitted defaults; preserving a generated full `cmd_config.json` from an initialized instance is safer than relying on a hand-maintained minimal file. [UNKNOWN: verify the exact config migration behavior if creating a brand-new minimal file against a future major version.]

### 3.5 Optional local T2I sidecar Compose shape

For HTML cards, the supported local deployment is a sidecar, not `t2i_strategy=local` in AstrBot:

```yaml
services:
  astrbot:
    image: soulter/astrbot:latest
    ports: ["6185:6185", "6199:6199"]
    environment:
      TZ: Asia/Shanghai
      ASTRBOT_DASHBOARD_INITIAL_PASSWORD: ${ASTRBOT_DASHBOARD_INITIAL_PASSWORD}
    volumes:
      - ./data:/AstrBot/data
    depends_on: [t2i]

  t2i:
    image: soulter/astrbot-t2i-service:latest
    expose: ["8999"]
```

Then `t2i_endpoint` is `http://t2i:8999` and AstrBot's strategy remains `remote`; the self-host guide explicitly recommends the service and endpoint approach ([`self-host-t2i.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/others/self-host-t2i.md)). The T2I service's `PORT` defaults to `8999` and its Dockerfile installs Chromium ([README](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/README.md), [Dockerfile](https://github.com/AstrBotDevs/astrbot-t2i-service/blob/bcc08e47274c69e5ace19bbf738b95260ce17ca7/Dockerfile)).

## 4. QQ adapters and configuration facts

### 4.1 OneBot v11 / aiocqhttp

AstrBot's adapter name/type is `aiocqhttp`, described as OneBot v11 with reverse WebSocket. Its config keys are:

```json
{
  "id": "default",
  "type": "aiocqhttp",
  "enable": true,
  "ws_reverse_host": "0.0.0.0",
  "ws_reverse_port": 6199,
  "ws_reverse_token": ""
}
```

The current adapter reads `ws_reverse_host`, `ws_reverse_port`, and optional `ws_reverse_token`, constructs `aiocqhttp.CQHttp(use_ws_reverse=True)`, and runs a reverse WebSocket server ([`aiocqhttp_platform_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)). The official guide says the protocol client connects to `ws(s)://<host>:6199/ws`, with AstrBot as server ([`aiocqhttp.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/platform/aiocqhttp.md)).

NapCat is listed by AstrBot as a OneBot v11 implementation; it is not a separate AstrBot adapter type. Lagrange is also handled as an external OneBot implementation, not a separate `type`; current aiocqhttp message parsing has URL/file branches labelled for Lagrange/NapCat ([`aiocqhttp.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/platform/aiocqhttp.md), [`aiocqhttp_platform_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py)).

### 4.2 QQ Official API

The WebSocket adapter name/type is `qq_official`. Current template keys are:

```json
{
  "id": "default",
  "type": "qq_official",
  "enable": true,
  "appid": "",
  "secret": "",
  "enable_group_c2c": true,
  "enable_guild_direct_message": true,
  "use_markdown": true
}
```

The adapter reads `appid`, `secret`, `enable_group_c2c`, `enable_guild_direct_message`, and optional/defaulted `use_markdown` ([`qqofficial_platform_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py)). The official guide documents WebSocket setup and the same manual fields ([`qqofficial/websockets.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/platform/qqofficial/websockets.md)).

The separate webhook adapter type is `qq_official_webhook`; the current config template includes:

```json
{
  "id": "default",
  "type": "qq_official_webhook",
  "enable": true,
  "appid": "",
  "secret": "",
  "use_markdown": true,
  "is_sandbox": false,
  "unified_webhook_mode": true,
  "webhook_uuid": "",
  "callback_server_host": "0.0.0.0",
  "port": 6196
}
```

Source: `CONFIG_METADATA_2.platform_group.metadata.platform.config_template` in [`astrbot/core/config/default.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/config/default.py).

The current source supports incoming `Reply` parsing for some QQ Official raw message-reference payloads, but the historical/current plugin guide's platform matrix marks QQ Official `Reply` sending unsupported; do not rely on quoted sending for QQ Official without a live adapter test ([`qqofficial_platform_adapter.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/platform/sources/qqofficial/qqofficial_platform_adapter.py), [old plugin guide](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/zh/dev/star/plugin.md)).

### 4.3 Adapter filter names

For plugin filters/metadata, use `aiocqhttp`, `qq_official`, and `qq_official_webhook`; these are keys of `ADAPTER_NAME_2_TYPE` ([`platform_adapter_type.py`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/astrbot/core/star/filter/platform_adapter_type.py)). NapCat and Lagrange do not appear as adapter keys because they speak OneBot v11 to `aiocqhttp`.

## 5. Transferable patterns from gsuid_core and WutheringWavesUID

These are framework-independent patterns only; their decorators/types are not AstrBot APIs.

### 5.1 Command organization

`gsuid_core` groups behavior under a named `SV` singleton and registers trigger methods such as `on_command`, `on_prefix`, `on_regex`, `on_file`, and `on_message`; command handlers receive a high-level `Bot` and `Event` and read `ev.text`, `ev.raw_text`, `ev.regex_dict`, and `ev.regex_group` ([`gsuid_core/sv.py`](https://github.com/Genshin-bots/gsuid_core/blob/c279708683109c62c8e690f6f8dbc70cbd4bf042/gsuid_core/sv.py), [trigger guidance](https://github.com/Genshin-bots/gsuid_core/blob/c279708683109c62c8e690f6f8dbc70cbd4bf042/.agents/skills/gscore-plugin-development/references/02-sv-and-triggers.md)). Transferable to AstrBot: keep one command-registration layer, give every command a clear trigger/permission/platform boundary, and separate trigger parsing from business logic. Do **not** copy `SV`, `Bot`, or `Event` types into AstrBot.

AstrBot's closest equivalent is `@filter.command`, `@filter.regex`, command groups, typed parameters, and optional platform/event filters ([`listen-message-event.md`](https://github.com/AstrBotDevs/AstrBot/blob/f99c76ec106f9d47a909393e463197687eec4070/docs/en/dev/star/guides/listen-message-event.md)).

### 5.2 Template/image-card rendering

WutheringWavesUID establishes a resource/template boundary: it builds a `jinja2.Environment` with `FileSystemLoader` over its templates directory ([`RESOURCE_PATH.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/utils/resource/RESOURCE_PATH.py)). Its representative rank/abyss cards, however, are generated procedurally with Pillow (`Image`, `ImageDraw`, layered assets), not directly through AstrBot HTML rendering ([`draw_all_rank_card.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/wutheringwaves_rank/draw_all_rank_card.py), [`draw_abyss_card.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/wutheringwaves_abyss/draw_abyss_card.py)). The transferable architecture is:

1. Fetch/normalize domain data.
2. Pass a typed view model into a renderer.
3. Keep assets/templates under a resource directory.
4. Return image bytes/path to the platform layer.
5. Add a consistent footer/brand/version to every card.

For alicedev, replace their Pillow renderer with AstrBot's `html_render` + Jinja2 template, and send the resulting `Image` component. Avoid copying their platform-specific `Bot.send` calls.

### 5.3 Pagination UX and long lists

WutheringWavesUID's rank command accepts an optional numeric page in its regex (`总排行` plus `(\d+)?` in the source's pattern), defaults to page 1, clamps to pages 1–5, and passes it to a renderer ([`wutheringwaves_rank/__init__.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/wutheringwaves_rank/__init__.py)). The renderer uses a fixed `page_num = 20`, computes an image height from the number of returned rows, and includes title/metadata/notes plus a footer ([`draw_all_rank_card.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/wutheringwaves_rank/draw_all_rank_card.py), [`image.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/utils/image.py)).

Transferable UX for alicedev lists:

- Parse an optional page argument (`/收藏 2` or `/需求列表 2`).
- Normalize invalid values to page 1 and cap the upper bound; do not let users request unbounded rows.
- Fetch `page_size` rows (for example 10–20), return total/page metadata, and render a card.
- Put `第 x / y 页`, command hint, and next/previous usage in a footer; retain a stable title and timestamp.
- Send one image rather than a long text message; keep the renderer independent of DuckDB and the platform adapter.

WutheringWavesUID's announcement data layer also requests bounded list sizes (`pageSize=5`) and separates list/detail fetches ([`wutheringwaves_ann/main.py`](https://github.com/CM-Edelweiss/WutheringWavesUID/blob/ec8696f078a8451104fe501c7a64a9812eb66996/WutheringWavesUID/wutheringwaves_ann/main.py)).

## Explicit unknowns/test probes

1. **Telegram @ by numeric ID:** current source uses `At.name` as textual `@username`; test a Telegram user with and without a username. Do not assume numeric-ID mentions work.
2. **Telegram quote + image in one chain:** source sets `reply_to_message_id` for each outgoing component; test the exact ordering `[Reply, At, Plain, Image]` with the target bot/API version.
3. **Relative/local images in HTML:** test a template with a data URI, an HTTPS URL, and a plugin-local relative path against the chosen sidecar. The sidecar's `file://` context and mount visibility determine behavior.
4. **QQ Official Reply:** inbound quote parsing exists in source, but the documented capability matrix marks Reply sending unsupported; test before promising quote semantics for that adapter.
5. **Minimal `cmd_config.json`:** AstrBot fills defaults and migrates config, but test a fresh container with a generated full config rather than relying on a hand-minimized file for production.
6. **Plugin config constructor:** verify the exact `__init__(context, config)` injection with a `_conf_schema.json` in the pinned runtime; the public docs and current `Star` signature support it, but this was not executed here.
7. **HTML image output lifetime:** when using `return_url=False`, retain/use the returned temporary path promptly; verify whether the event cleanup or temp cleanup removes it after send.
