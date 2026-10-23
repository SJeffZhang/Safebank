"""Argon2 PIN verification and persistent wall-clock login throttles (§7.3)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from safebank_contracts.codes import Code
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import tables as t
from ..errors import GatewayError

HASHER = PasswordHasher()
CUSTOMER_LABELS = {"cus_zs": "张三", "cus_ls": "李四", "cus_sb": "孙八", "cus_ww": "王五"}


def _validated_hashes(values: object, *, plaintext: bool) -> dict[str, str]:
    """Share customer/value validation and the Argon2 implementation across loaders."""
    if not isinstance(values, dict):
        raise ValueError("invalid PIN configuration")
    hashes = {}
    for customer, value in values.items():
        if customer not in CUSTOMER_LABELS or not isinstance(value, str):
            raise ValueError("invalid PIN configuration")
        if plaintext:
            if not 1 <= len(value) <= 128:
                raise ValueError("invalid PIN configuration")
            value = HASHER.hash(value)
        # Parse the actual encoding rather than accepting a nominal prefix.
        HASHER.check_needs_rehash(value)
        hashes[customer] = value
    return hashes


def configured_hashes_from_file(pins_file: str) -> dict[str, str]:
    """Load exactly this startup file, ignoring ambient secrets and legacy cache.

    No cross-startup cache: a later startup may replace contents at the same path.
    The runtime retains this returned in-memory snapshot for its own lifetime.
    """
    try:
        pins = json.loads(Path(pins_file).read_text())
        return _validated_hashes(pins, plaintext=True)
    except (OSError, ValueError, TypeError, InvalidHashError) as exc:
        raise GatewayError(
            Code.DEPENDENCY_UNAVAILABLE, 503, message="PIN configuration unavailable"
        ) from exc


@lru_cache(maxsize=1)
def configured_hashes() -> dict[str, str]:
    """Legacy zero-argument startup entrypoint; explicit runtime paths use the loader above."""
    encoded = os.getenv("DEMO_PIN_HASHES")
    if not encoded:
        return configured_hashes_from_file(os.getenv("DEMO_PINS_FILE", "/run/secrets/demo_pins"))
    try:
        return _validated_hashes(json.loads(encoded), plaintext=False)
    except (ValueError, TypeError, InvalidHashError) as exc:
        raise GatewayError(
            Code.DEPENDENCY_UNAVAILABLE, 503, message="PIN configuration unavailable"
        ) from exc


def verify_pin(pin: str, encoded: str | None) -> bool:
    # A dummy verification keeps unknown customers on the same expensive path.
    try:
        return HASHER.verify(encoded or _dummy_hash(), pin) and encoded is not None
    except (VerificationError, InvalidHashError):
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return HASHER.hash("unusable-demo-customer")


async def authenticate(
    conn: AsyncConnection,
    customer_id: str,
    pin: str,
    ip: str,
    now: datetime,
    hashes: dict[str, str],
) -> Code | None:
    """Return a failure code so the caller commits throttle state before raising.

    Serialize by IP then customer row; a failed attempt must survive the HTTP
    error, hence this function deliberately does not raise authentication errors.
    """
    await conn.execute(
        insert(t.ip_ratelimit)
        .values(
            ip=ip,
            window_started_at_wall=now,
            count=0,
        )
        .on_conflict_do_nothing()
    )
    iprow = (
        (
            await conn.execute(
                select(t.ip_ratelimit).where(t.ip_ratelimit.c.ip == ip).with_for_update()
            )
        )
        .mappings()
        .one()
    )
    reset = now >= iprow["window_started_at_wall"] + timedelta(minutes=1)
    count = 0 if reset else iprow["count"]
    if count >= 10:
        return Code.RATE_LIMITED
    await conn.execute(
        update(t.ip_ratelimit)
        .where(t.ip_ratelimit.c.ip == ip)
        .values(
            count=count + 1,
            window_started_at_wall=now if reset else iprow["window_started_at_wall"],
        )
    )
    await conn.execute(
        insert(t.pin_failures)
        .values(customer_id=customer_id, failed_count=0)
        .on_conflict_do_nothing()
    )
    row = (
        (
            await conn.execute(
                select(t.pin_failures)
                .where(t.pin_failures.c.customer_id == customer_id)
                .with_for_update()
            )
        )
        .mappings()
        .one()
    )
    locked = row["locked_until_wall"]
    if locked and now < locked:
        return Code.RATE_LIMITED
    if verify_pin(pin, hashes.get(customer_id)):
        await conn.execute(
            update(t.pin_failures)
            .where(t.pin_failures.c.customer_id == customer_id)
            .values(failed_count=0, locked_until_wall=None, last_failed_at_wall=None)
        )
        return None
    failures = (0 if locked else row["failed_count"]) + 1
    await conn.execute(
        update(t.pin_failures)
        .where(t.pin_failures.c.customer_id == customer_id)
        .values(
            failed_count=failures,
            last_failed_at_wall=now,
            locked_until_wall=now + timedelta(minutes=15) if failures >= 5 else None,
        )
    )
    return Code.RATE_LIMITED if failures >= 5 else Code.UNAUTHENTICATED
