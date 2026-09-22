"""Identifier generation for alicedev domain keys (§2 of ARCHITECTURE)."""

from __future__ import annotations

import secrets

_B32 = "abcdefghijklmnopqrstuvwxyz234567"  # RFC 4648 lowercase base32 alphabet


def _base32(n_chars: int) -> str:
    return "".join(secrets.choice(_B32) for _ in range(n_chars))


def new_agent_ref() -> str:
    return "a_" + _base32(10)


def new_msg_ref() -> str:
    return "m_" + _base32(10)


def new_report_id() -> str:
    return "r_" + _base32(26)  # 26 * 5 = 130 bit of entropy


def new_token() -> str:
    return secrets.token_urlsafe(32)
