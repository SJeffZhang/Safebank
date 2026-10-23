"""Evaluation append interface (W3 implements; W4 calls on re-evaluation).

Every evaluation (initial G4 or execute-time replay) is append-only with a
monotonic ``revision`` (``UNIQUE(plan_id, revision)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncConnection

if TYPE_CHECKING:  # pragma: no cover
    from ..pipeline.context import EvalContext


async def append_evaluation(conn: AsyncConnection, ctx: EvalContext) -> Any:
    """Append an evaluation row (revision+1) for the plan and return it."""
    raise NotImplementedError("append_evaluation is W3 (PRD §7.2, §7.10.5)")
