"""Download a Telegram message's media using the shared tg-cli session.

The installed kabi-tg-cli exposes text/query commands but not media download.
This small companion keeps the contract's stable ``tg photo --msg --out``
entrypoint while reusing tg-cli's DATA_DIR, API credentials, and Telethon
session file.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

from tg_cli.client import connect
from tg_cli.config import get_db_path


def _chat_rows(msg_id: int) -> list[tuple[int, str | None]]:
    with sqlite3.connect(get_db_path()) as conn:
        try:
            rows = conn.execute(
                "SELECT DISTINCT chat_id, chat_name FROM messages WHERE msg_id = ?",
                (msg_id,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            rows = []
    return [(int(chat_id), chat_name) for chat_id, chat_name in rows]


async def _download(msg_id: int, output: Path) -> Path:
    rows = _chat_rows(msg_id)
    if not rows:
        raise RuntimeError(
            f"message {msg_id} is not in the local tg-cli cache; run `tg history` or `tg sync` first"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    async with connect() as client:
        for chat_id, chat_name in rows:
            entities: list[object] = [chat_id]
            if chat_name:
                entities.append(chat_name)
            for entity_ref in entities:
                try:
                    message = await client.get_messages(entity_ref, ids=msg_id)
                except Exception:
                    continue
                if message is None:
                    continue
                if not message.media:
                    raise RuntimeError(f"message {msg_id} has no downloadable media")
                downloaded = await client.download_media(message, file=str(output))
                if downloaded:
                    return Path(downloaded)

    raise RuntimeError(f"message {msg_id} could not be resolved in Telegram")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download media from a cached Telegram message")
    parser.add_argument("photo", choices=["photo"])
    parser.add_argument("--msg", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(_download(args.msg, args.out))
    except Exception as exc:
        print(f"tg photo: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
