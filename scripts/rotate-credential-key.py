"""Re-encrypt every stored credential under the current key.

A rotation is three steps, and skipping the middle one loses data:

    1. Put the new key in PLIMSOLL_CREDENTIAL_KEY and move the outgoing key
       into PLIMSOLL_CREDENTIAL_KEYS_RETIRED. Restart. Everything still opens,
       because a retired key still decrypts what it wrote.
    2. Run this. Every row is read with whichever key opens it and written back
       under the current one.
    3. Remove the retired key and restart. Nothing references it any more.

Between steps one and three the deployment holds both keys, which is the point:
a rotation that requires an outage is a rotation that does not happen.

Run with an operator connection, not the application's. Row-level security is
FORCEd on every tenant table -- it applies to the table owner too, and only a
superuser is outside it -- so a maintenance job that has to touch every
organisation's rows uses the same class of credential `pg_dump` does. That is
the security model working rather than an obstacle to it: nothing reachable
from the application can read across organisations, maintenance included.

    PLIMSOLL_ROTATION_DATABASE_URL=postgresql+asyncpg://postgres:...@host/plimsoll \\
      uv run python scripts/rotate-credential-key.py
"""

from __future__ import annotations

import asyncio
import os

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from plimsoll_api.security.secrets import get_key_provider


async def rotate() -> int:
    dsn = os.environ.get("PLIMSOLL_ROTATION_DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "Set PLIMSOLL_ROTATION_DATABASE_URL to an operator connection. "
            "Row-level security is forced on every tenant table, so the "
            "application's own role cannot see across organisations -- which is "
            "the point of it."
        )

    provider = get_key_provider()
    engine = create_async_engine(dsn)
    rotated = 0

    try:
        async with engine.begin() as session:
            rows = (
                await session.execute(sa.text("SELECT id, ciphertext, key_ref FROM credentials"))
            ).all()

            for row in rows:
                # Decrypting first means a key that cannot open a row stops the
                # rotation before anything is written, rather than half way
                # through with no way back.
                plaintext = provider.decrypt(bytes(row.ciphertext), row.key_ref)
                ciphertext, key_ref = provider.encrypt(plaintext)
                if key_ref == row.key_ref:
                    continue
                await session.execute(
                    sa.text(
                        "UPDATE credentials "
                        "SET ciphertext = :ciphertext, key_ref = :key_ref "
                        "WHERE id = :id"
                    ),
                    {"ciphertext": ciphertext, "key_ref": key_ref, "id": row.id},
                )
                rotated += 1
    finally:
        await engine.dispose()

    return rotated


if __name__ == "__main__":
    moved = asyncio.run(rotate())
    print(f"Re-encrypted {moved} credential(s) under the current key.")
