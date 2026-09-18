"""One-time share tokens and stateless HMAC cookie authorization."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from typing import Mapping


COOKIE_NAME = "alicedev_s"
COOKIE_MAX_AGE = 2_592_000  # 30 days
TOKEN_TTL_MAX = 21_600  # six hours
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_BASE_TARGET_PATTERN = re.compile(
    r"^/h/[^/]+/workspace/[^/?]+\?open=agent%3A[^&]+$"
)
_REPORT_ID_PATTERN = re.compile(r"^r_[a-z2-7]{26}$")


@dataclass(frozen=True, slots=True)
class TokenRecord:
    target: str
    user_key: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    target: str
    user_key: str
    expires_at: float


class TokenTable:
    """An event-loop-local, single-consumer token table.

    ``consume`` deliberately performs ``dict.pop`` and expiry checking without an
    await.  That makes concurrent aiohttp handlers race on one atomic event-loop
    section: only one request can obtain a record.
    """

    def __init__(self) -> None:
        self._records: dict[str, TokenRecord] = {}

    @staticmethod
    def validate_target(target: str) -> bool:
        if not isinstance(target, str):
            return False
        base_target = target.removesuffix("&embed=1")
        return bool(_BASE_TARGET_PATTERN.fullmatch(base_target)) and (
            target == base_target or target == f"{base_target}&embed=1"
        )

    @staticmethod
    def validate_token(token: str) -> bool:
        return bool(_TOKEN_PATTERN.fullmatch(token))

    def issue(
        self,
        *,
        target: str,
        user_key: str,
        ttl_s: int | float | None = None,
        now: float | None = None,
    ) -> IssuedToken:
        if not self.validate_target(target):
            raise ValueError("target does not match the workspace URL contract")
        if not isinstance(user_key, str) or not user_key.strip():
            raise ValueError("user_key must be non-empty")

        ttl = TOKEN_TTL_MAX if ttl_s is None else _coerce_ttl(ttl_s)
        issued_at = time.time() if now is None else now
        expires_at = issued_at + min(ttl, TOKEN_TTL_MAX)
        token = secrets.token_urlsafe(32)
        while token in self._records:
            token = secrets.token_urlsafe(32)
        record = TokenRecord(target=target, user_key=user_key, expires_at=expires_at)
        self._records[token] = record
        return IssuedToken(
            token=token,
            target=target,
            user_key=user_key,
            expires_at=expires_at,
        )

    def consume(self, token: str, *, now: float | None = None) -> TokenRecord | None:
        record = self._records.pop(token, None)
        if record is None:
            return None
        current = time.time() if now is None else now
        if record.expires_at <= current:
            return None
        return record

    def sweep(self, *, now: float | None = None) -> int:
        current = time.time() if now is None else now
        expired = [
            token for token, record in self._records.items() if record.expires_at <= current
        ]
        for token in expired:
            self._records.pop(token, None)
        return len(expired)

    def __len__(self) -> int:
        return len(self._records)


def _coerce_ttl(value: int | float) -> int:
    if isinstance(value, bool):
        raise ValueError("ttl_s must be a positive number")
    try:
        ttl = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("ttl_s must be a positive number") from exc
    if ttl <= 0:
        raise ValueError("ttl_s must be a positive number")
    return min(ttl, TOKEN_TTL_MAX)


class SignedCookieCodec:
    """Encode and verify the gateway's stateless HMAC authorization cookie."""

    def __init__(self, secret: bytes) -> None:
        if not secret:
            raise ValueError("cookie signing secret must not be empty")
        self._secret = secret

    def issue(self, *, user_key: str, now: float | None = None) -> str:
        if not user_key:
            raise ValueError("user_key must be non-empty")
        issued_at = int(time.time() if now is None else now)
        payload = {"iat": issued_at, "exp": issued_at + COOKIE_MAX_AGE, "sub": user_key}
        encoded_payload = _encode_json(payload)
        signature = self._sign(encoded_payload)
        return f"{encoded_payload}.{signature}"

    def verify(self, value: str | None, *, now: float | None = None) -> bool:
        if not value or value.count(".") != 1:
            return False
        encoded_payload, supplied_signature = value.split(".", 1)
        if not encoded_payload or not supplied_signature:
            return False
        expected_signature = self._sign(encoded_payload)
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False
        payload = _decode_json(encoded_payload)
        if payload is None:
            return False
        issued_at = payload.get("iat")
        expires_at = payload.get("exp")
        subject = payload.get("sub")
        if (
            not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or not isinstance(expires_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(subject, str)
            or not subject
            or expires_at <= issued_at
        ):
            return False
        current = int(time.time() if now is None else now)
        return expires_at > current

    def _sign(self, encoded_payload: str) -> str:
        digest = hmac.new(
            self._secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return _b64url_encode(digest)


def _encode_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _b64url_encode(encoded)


def _decode_json(value: str) -> dict[str, object] | None:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        decoded = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
