"""Customer notifications (PRD §7.12), written inside the producer's transaction.

Title and body are fixed L2 templates: no user, model or L3 free text is copied in.
UNIQUE(event_id, type) makes every producer idempotent under retries and replays.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from . import tables as t

NotificationType = Literal["APPROVAL_PENDING", "EXECUTION_RESULT"]

_TEMPLATES: dict[tuple[str, str], tuple[str, str]] = {
    ("APPROVAL_PENDING", "PENDING"): (
        "有一项操作待您确认",
        "请在安全中心查看确认面板后再决定是否执行。",
    ),
    ("EXECUTION_RESULT", "EXECUTED"): (
        "操作已完成",
        "您确认的操作已执行完成，可在安全中心查看审计记录。",
    ),
    ("EXECUTION_RESULT", "FAILED"): (
        "操作未完成",
        "您确认的操作未能执行，资金与卡片状态未发生变化。",
    ),
}


async def notify(
    conn: AsyncConnection,
    *,
    customer_id: str,
    event_id: str,
    type: NotificationType,
    outcome: str,
    plan_id: str | None = None,
) -> None:
    # Delayed import avoids state -> notify -> utterance.service -> state.
    from .utterance.service import new_id

    title, body = _TEMPLATES[(type, outcome)]
    payload = {"title": title, "body": body, "outcome": outcome}
    if plan_id is not None:
        payload["related_plan_id"] = plan_id
    await conn.execute(
        insert(t.notifications)
        .values(
            id=new_id("ntf"),
            customer_id=customer_id,
            event_id=event_id,
            type=type,
            payload=payload,
            created_at=text("now()"),
        )
        .on_conflict_do_nothing(index_elements=["event_id", "type"])
    )
