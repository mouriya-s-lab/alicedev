"""Client for the gateway's internal one-time-token endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import aiohttp


_DEFAULT_TTL_S: Final[int] = 21600


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """Token and public URL returned by the gateway."""

    token: str
    url: str


class GatewayError(RuntimeError):
    """The gateway rejected a token request or returned an invalid response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GatewayClient:
    """Issue sharing links through the gateway internal API."""

    def __init__(
        self,
        gateway_url: str,
        internal_token: str,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._internal_token = internal_token
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def issue_token(
        self,
        target: str,
        user_key: str,
        ttl_s: int = _DEFAULT_TTL_S,
    ) -> IssuedToken:
        """Register a target and return its one-time public URL."""

        if not self._gateway_url:
            raise GatewayError("gateway URL is not configured")
        if not self._internal_token:
            raise GatewayError("gateway internal token is not configured")
        if ttl_s <= 0 or ttl_s > _DEFAULT_TTL_S:
            raise ValueError("gateway token TTL must be between 1 and 21600 seconds")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Alicedev-Token": self._internal_token,
        }
        payload = {"target": target, "user_key": user_key, "ttl_s": ttl_s}
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    f"{self._gateway_url}/internal/tokens",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status >= 400:
                        detail = await response.text()
                        raise GatewayError(
                            f"gateway returned {response.status}: {_short_detail(detail)}",
                            status=response.status,
                        )
                    try:
                        result = await response.json()
                    except (aiohttp.ContentTypeError, ValueError) as exc:
                        raise GatewayError("gateway returned invalid JSON") from exc
        except GatewayError:
            raise
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise GatewayError(f"gateway request failed: {exc}") from exc

        if not isinstance(result, Mapping):
            raise GatewayError("gateway returned a non-object response")
        token = result.get("token")
        url = result.get("url")
        if not isinstance(token, str) or not token:
            raise GatewayError("gateway response has no token")
        if not isinstance(url, str) or not url:
            raise GatewayError("gateway response has no URL")
        return IssuedToken(token=token, url=url)


def _short_detail(detail: str) -> str:
    detail = detail.strip()
    return detail if len(detail) <= 240 else detail[:237] + "..."
