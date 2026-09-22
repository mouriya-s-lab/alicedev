"""Jinja2-backed HTML card rendering through AstrBot's t2i service."""

from pathlib import Path
from typing import Any, Mapping, Protocol

import jinja2


class HtmlRenderHost(Protocol):
    async def html_render(
        self,
        tmpl: str,
        data: dict[str, Any],
        *,
        return_url: bool = True,
        options: dict[str, Any] | None = None,
    ) -> str: ...


class CardRenderer:
    """Render a named ``templates/cards/*.html`` card into a local PNG path.

    The template is rendered once in the plugin process so fields can be
    escaped and image URLs can be normalized before the HTML crosses the
    AstrBot/t2i boundary. The resulting HTML is then handed to AstrBot's
    ``Star.html_render`` with ``return_url=False``; the sidecar downloads the
    browser screenshot to a local temporary file for the caller.
    """

    _DEFAULT_FIELDS: dict[str, Any] = {
        "title": "alicedev",
        "eyebrow": "",
        "subtitle": "",
        "text": "",
        "body": "",
        "requirement": "",
        "meta": "",
        "footer": "",
        "rows": (),
        "images": (),
    }

    def __init__(self, host: HtmlRenderHost, templates_root: Path) -> None:
        self._host = host
        self._cards_root = Path(templates_root) / "cards"
        self._environment = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._cards_root)),
            autoescape=jinja2.select_autoescape(("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    async def render(self, template: str, fields: Mapping[str, Any]) -> Path:
        """Render ``template`` and return a local PNG path.

        ``template`` is deliberately restricted to one filename. This prevents
        command/user input from escaping ``templates/cards`` and makes the
        template registry easy to audit.
        """

        name = template[:-5] if template.endswith(".html") else template
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("card template must be a simple filename")
        path = (self._cards_root / f"{name}.html").resolve()
        root = self._cards_root.resolve()
        if root not in path.parents:
            raise ValueError("card template is outside templates/cards")
        if not path.is_file():
            raise FileNotFoundError(path)

        values = dict(self._DEFAULT_FIELDS)
        values.update(fields)
        html = self._environment.get_template(path.name).render(**values)
        result = await self._host.html_render(
            html,
            {},
            return_url=False,
            options={"type": "png", "full_page": True, "viewport_width": 800},
        )
        local_path = Path(result)
        if not local_path.is_file():
            raise RuntimeError(
                "Star.html_render(return_url=False) did not return a local image path: "
                f"{result!r}"
            )
        return local_path
