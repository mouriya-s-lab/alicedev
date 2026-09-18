"""Parse GitHub issue/PR links and the configured ``#number`` shorthand."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .models import GithubKind, GithubRef

_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_OWNER_OR_REPO = re.compile(r"^[A-Za-z0-9_.-]+$")
_NUMBER = re.compile(r"^[1-9][0-9]*$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})


def parse_repository(value: str) -> tuple[str, str]:
    """Validate and split an ``owner/repository`` setting."""

    repository = value.strip()
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("default repository must be owner/repository")
    owner, repo = repository.split("/", 1)
    return owner, repo


def parse_github_ref(text: str, default_repo: str | None = None) -> GithubRef | None:
    """Parse one exact GitHub URL or ``#number`` shorthand.

    The parser intentionally accepts only a complete message/reference.  This
    lets the dispatcher use it for the URL-only trigger without accidentally
    interpreting prose containing a link as a command.
    """

    value = text.strip()
    if not value:
        return None

    if value.startswith("#"):
        number_text = value[1:]
        if not _NUMBER.fullmatch(number_text) or default_repo is None:
            return None
        try:
            owner, repo = parse_repository(default_repo)
        except ValueError:
            return None
        return GithubRef(owner=owner, repo=repo, number=int(number_text))

    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _GITHUB_HOSTS:
        return None
    if parsed.username is not None or parsed.password is not None or parsed.port is not None:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) != 4:
        return None
    owner, repo, resource, number_text = segments
    if not _OWNER_OR_REPO.fullmatch(owner) or not _OWNER_OR_REPO.fullmatch(repo):
        return None
    if resource == "issues":
        kind = GithubKind.ISSUE
    elif resource in {"pull", "pulls"}:
        kind = GithubKind.PR
    else:
        return None
    if not _NUMBER.fullmatch(number_text):
        return None
    return GithubRef(owner=owner, repo=repo, number=int(number_text), kind=kind)
