"""Explicit trusted CLI fixture install; never expose database reset over HTTP."""

import asyncio
import os

from sqlalchemy import text

from .db import engine
from .seed import seed_core


async def main() -> None:
    db = engine(os.environ["BANK_DATABASE_URL"])
    try:
        async with db.begin() as conn:
            await conn.execute(text("SELECT pg_advisory_xact_lock(814001)"))
            created = await seed_core(conn, os.environ["FINGERPRINT_KEY"])
        print("P1 core fixture installed" if created else "Existing dataset preserved")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(main())
