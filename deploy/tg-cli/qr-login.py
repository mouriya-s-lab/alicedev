"""Log the container's tg-cli session in via Telegram QR login.

Run inside alicedev-tg-cli:  ``docker exec -i alicedev-tg-cli python /opt/alicedev/tg-qr-login.py``

Prints one line ``QR <tg://login?token=...>`` and waits (up to --timeout
seconds) for an already-authorized client of the same account to accept the
token (``tools/tg-approve-login`` on the operator machine). The result is a new,
independent authorization for this container: no session file is shared, so
Telegram never sees one auth key used from two places.

Exit codes: 0 authorized (prints ``AUTHORIZED <user id>``), 3 the account has
two-step verification (password needed; fall back to copying a session file),
4 timed out.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from tg_cli.config import get_api_hash, get_api_id, get_session_path


async def main(timeout: float) -> int:
    client = TelegramClient(get_session_path(), get_api_id(), get_api_hash())
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"AUTHORIZED {me.id} (already)", flush=True)
            return 0
        qr = await client.qr_login()
        print(f"QR {qr.url}", flush=True)
        try:
            await qr.wait(timeout=timeout)
        except SessionPasswordNeededError:
            print("PASSWORD_NEEDED", flush=True)
            return 3
        except asyncio.TimeoutError:
            print("TIMEOUT", flush=True)
            return 4
        me = await client.get_me()
        print(f"AUTHORIZED {me.id}", flush=True)
        return 0
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=180.0)
    sys.exit(asyncio.run(main(parser.parse_args().timeout)))
