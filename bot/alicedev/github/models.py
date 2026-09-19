"""Typed GitHub references and fetched items used by alicedev commands."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote


class GithubKind(str, Enum):
    """The two GitHub resources understood by alicedev.

    Values intentionally match ``TemplateRegistry`` link trigger keys so a
    parsed URL can be routed without a second string mapping.
    """

    ISSUE = "github_issue"
    PR = "github_pr"

    @property
    def resource_name(self) -> str:
        return "pr" if self is GithubKind.PR else "issue"

    @property
    def path_segment(self) -> str:
        return "pull" if self is GithubKind.PR else "issues"


@dataclass(frozen=True, slots=True)
class GithubRef:
    """A repository item reference.

    ``kind`` is ``None`` for the shorthand ``#123`` form.  GitHub's issues
    endpoint is used for that form and the response determines whether the
    item is an issue or a pull request.
    """

    owner: str
    repo: str
    number: int
    kind: GithubKind | None = None

    def __post_init__(self) -> None:
        if not self.owner or not self.repo:
            raise ValueError("GitHub owner and repository are required")
        if self.number <= 0:
            raise ValueError("GitHub item number must be positive")

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        segment = self.kind.path_segment if self.kind is not None else "issues"
        return f"https://github.com/{self.owner}/{self.repo}/{segment}/{self.number}"

    @property
    def link_kind(self) -> str | None:
        """TemplateRegistry trigger key for a URL with a known resource kind."""

        return self.kind.value if self.kind is not None else None

    @property
    def api_path(self) -> str:
        segment = "pulls" if self.kind is GithubKind.PR else "issues"
        repository = quote(self.repository, safe="/")
        return f"/repos/{repository}/{segment}/{self.number}"


@dataclass(frozen=True, slots=True)
class GithubItem:
    """The stable subset of GitHub issue/PR data exposed to templates."""

    kind: GithubKind
    owner: str
    repo: str
    number: int
    title: str
    body: str
    labels: tuple[str, ...]
    state: str
    url: str

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"

