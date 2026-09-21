"""Jinja2-backed HTML card rendering through AstrBot's t2i service."""

from pathlib import Path
from shutil import copyfile
from typing import Any, Mapping, Protocol

import jinja2

from alicedev.images import image_data_uri


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

    async def render_upgrade_explanation(
        self,
        summary: Mapping[str, Any],
        *,
        output_path: Path | None = None,
    ) -> Path:
        """Render a product-level change explanation, never a code diff.

        ``summary`` is a structured mapping with optional ``title``,
        ``subtitle``, ``overview``, ``user_impact``, ``highlights`` (a list of
        strings or ``{"label", "value"}`` mappings), ``risk`` and ``footer``.
        When ``output_path`` is supplied the PNG is copied there so it can be
        placed on the shared reports volume and sent through
        ``ReplyRenderer.render_inline_image``.
        """
        fields = dict(summary)
        fields.setdefault("eyebrow", "产品变化说明")
        fields.setdefault("title", "产品变更说明")
        fields.setdefault("overview", fields.get("summary") or fields.get("text") or "")
        fields.setdefault("user_impact", fields.get("impact") or "")
        fields.setdefault("highlights", fields.get("changes") or ())
        return await self._render_upgrade_card(
            "upgrade_explanation", fields, output_path=output_path
        )

    async def render_upgrade_evidence(
        self,
        before: Mapping[str, Any] | str | Path,
        after: Mapping[str, Any] | str | Path,
        *,
        run_id: str = "",
        baseline_sha: str = "",
        candidate_sha: str = "",
        scenario: str = "",
        captured_at: str = "",
        output_path: Path | None = None,
    ) -> Path:
        """Render one before/after test-evidence card.

        Capture mappings may use ``image`` (or ``path``/``capture_path``) for
        a local PNG and may add ``label``, ``status`` and ``detail``. Local
        paths are converted to data URIs before crossing the t2i boundary;
        callers should put the returned/copy destination below the shared
        reports root when it will be delivered via the callback image path.
        """
        before_fields = self._capture_fields(before, "线上版本")
        after_fields = self._capture_fields(after, "候选版本")
        fields = {
            "eyebrow": "对称 E2E 证据",
            "title": "上线前后验证",
            "before": before_fields,
            "after": after_fields,
            "run_id": run_id,
            "baseline_sha": baseline_sha,
            "candidate_sha": candidate_sha,
            "scenario": scenario,
            "captured_at": captured_at,
        }
        return await self._render_upgrade_card(
            "upgrade_evidence", fields, output_path=output_path
        )

    async def _render_upgrade_card(
        self,
        template: str,
        fields: Mapping[str, Any],
        *,
        output_path: Path | None,
    ) -> Path:
        rendered = await self.render(template, fields)
        if output_path is None:
            return rendered
        destination = Path(output_path).expanduser()
        if destination.suffix.lower() != ".png":
            raise ValueError("upgrade card output_path must end in .png")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if rendered.resolve() != destination.resolve():
            copyfile(rendered, destination)
        return destination

    @staticmethod
    def _capture_fields(
        capture: Mapping[str, Any] | str | Path,
        default_label: str,
    ) -> dict[str, Any]:
        if isinstance(capture, Mapping):
            fields = dict(capture)
            reference = fields.get("image") or fields.get("path") or fields.get("capture_path")
        else:
            fields = {}
            reference = capture
        if not isinstance(reference, (str, Path)) or not str(reference).strip():
            raise ValueError("before/after capture requires an image path or data URI")
        source = str(reference)
        if source.startswith("data:image/"):
            fields["image"] = source
        else:
            fields["image"] = image_data_uri(Path(source).expanduser())
        fields.setdefault("label", default_label)
        fields.setdefault("status", "")
        fields.setdefault("detail", "")
        return fields
