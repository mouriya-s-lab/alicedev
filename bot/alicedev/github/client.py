"""GitHub REST prefetch for alicedev interpretation prompts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import aiohttp

from .models import GithubItem, GithubKind, GithubRef
from .parser import parse_github_ref

_GITHUB_API: Final[str] = "https://api.github.com"
_ACCEPT: Final[str] = "application/vnd.github+json"
_API_VERSION: Final[str] = "2022-11-28"
_USER_AGENT: Final[str] = "alicedev"


class GithubFetchError(RuntimeError):
    """A GitHub API request failed or returned an unexpected payload."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GithubClient:
    """Fetch issues and pull requests using GitHub's REST API."""

    def __init__(
        self,
        token: str | None = None,
        default_repo: str = "TraderAlice/OpenAlice",
        *,
        api_base: str = _GITHUB_API,
        timeout_s: float = 15.0,
    ) -> None:
        self._token = token.strip() if token else None
        self.default_repo = default_repo
        self._api_base = api_base.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    def parse(self, text: str) -> GithubRef | None:
        """Parse with this client's configured default repository."""

        return parse_github_ref(text, self.default_repo)

    def try_parse(self, text: str) -> GithubRef | None:
        """Dispatcher-facing alias for parsing a complete message/reference."""

        return self.parse(text)

    @staticmethod
    def to_template_vars(item: GithubItem) -> "TemplateVars":
        """Build the registry's typed Jinja context for a fetched item."""

        from alicedev.templates.registry import GithubVar, TemplateVars

        return TemplateVars(
            github=GithubVar(
                kind=item.kind.value,
                owner=item.owner,
                repo=item.repo,
                number=item.number,
                title=item.title,
                body=item.body,
                labels=item.labels,
                state=item.state,
                url=item.url,
            )
        )

    async def fetch(self, ref: GithubRef) -> GithubItem:
        """Fetch one issue or PR and normalize it into :class:`GithubItem`."""

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            payload = await self._get(session, ref)
        return self._decode_item(ref, payload)

    async def _get(
        self,
        session: aiohttp.ClientSession,
        ref: GithubRef,
    ) -> Mapping[str, object]:
        endpoint = ref.api_path
        url = f"{self._api_base}{endpoint}"
        headers = {
            "Accept": _ACCEPT,
            "X-GitHub-Api-Version": _API_VERSION,
            "User-Agent": _USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with session.get(url, headers=headers) as response:
                if response.status >= 400:
                    detail = await response.text()
                    message = _api_error_message(detail)
                    raise GithubFetchError(
                        f"GitHub API returned {response.status}: {message}",
                        status=response.status,
                    )
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise GithubFetchError("GitHub API returned invalid JSON") from exc
        except GithubFetchError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise GithubFetchError(f"GitHub API request failed: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise GithubFetchError("GitHub API returned a non-object payload")
        return payload

    @staticmethod
    def _decode_item(ref: GithubRef, payload: Mapping[str, object]) -> GithubItem:
        title = _required_string(payload, "title")
        body = _optional_string(payload, "body")
        state = _required_string(payload, "state")
        number = _required_int(payload, "number", fallback=ref.number)
        url = _optional_string(payload, "html_url") or ref.url
        labels = _labels(payload.get("labels"))

        # The /issues endpoint represents pull requests with a ``pull_request``
        # object, including when a PR was copied as an ``/issues/<n>`` URL.
        # Explicit /pull URLs already carry the same type information.
        kind = ref.kind
        if isinstance(payload.get("pull_request"), Mapping):
            kind = GithubKind.PR
        elif kind is None:
            kind = GithubKind.ISSUE

        return GithubItem(
            kind=kind,
            owner=ref.owner,
            repo=ref.repo,
            number=number,
            title=title,
            body=body,
            labels=labels,
            state=state,
            url=url,
        )


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise GithubFetchError(f"GitHub API payload missing string field: {key}")
    return value


def _optional_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GithubFetchError(f"GitHub API payload has invalid string field: {key}")
    return value


def _required_int(payload: Mapping[str, object], key: str, *, fallback: int) -> int:
    value = payload.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GithubFetchError(f"GitHub API payload has invalid integer field: {key}")
    return value


def _labels(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise GithubFetchError("GitHub API payload has invalid labels field")
    result: list[str] = []
    for label in value:
        if not isinstance(label, Mapping):
            raise GithubFetchError("GitHub API payload has invalid label")
        name = label.get("name")
        if not isinstance(name, str):
            raise GithubFetchError("GitHub API payload label has no name")
        result.append(name)
    return tuple(result)


def _api_error_message(detail: str) -> str:
    # Error bodies are untrusted and may be large; keep command errors concise.
    if len(detail) > 240:
        detail = detail[:237] + "..."
    return detail.strip() or "request failed"
