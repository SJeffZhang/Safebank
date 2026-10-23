"""Approval creation interface (W4 implements; W3 calls from G4).

W3 calls this when decision == REQUIRE_APPROVAL; W4 owns the binding_mac /
view_hash computation and the row write.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection


async def create_for_evaluation(
    conn: AsyncConnection, *, plan: Any, evaluation: Any, view: Any
) -> str | None:
    """Create an approval for a REQUIRE_APPROVAL evaluation.

    Returns the new ``approval_id`` (never None for a REQUIRE_APPROVAL call).
    """
    raise NotImplementedError("create_for_evaluation is W4 (PRD §7.10)")
