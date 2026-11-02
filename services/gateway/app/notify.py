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

NotificationType = Literal[
    "APPROVAL_PENDING",
    "EXECUTION_RESULT",
    "CHARGE_UPCOMING",
    "CHARGE_BLOCKED",
    "SCHEDULE_RESULT",
    "FUND_RESULT",
]

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


_TEMPLATES.update(
    {
        ("SCHEDULE_RESULT", "SUCCESS"): (
            "定时转账已完成",
            "本期定时转账已执行，可在交易记录中核对。",
        ),
        ("SCHEDULE_RESULT", "BLOCKED"): (
            "定时转账未执行",
            "本期定时转账未通过安全检查，请查看安全记录。",
        ),
        ("SCHEDULE_RESULT", "FAILED_INSUFFICIENT"): (
            "定时转账未执行",
            "账户余额不足，本期定时转账未完成。",
        ),
        ("SCHEDULE_RESULT", "SKIPPED_REAPPROVAL"): (
            "定时转账需要重新确认",
            "本期定时转账未执行，请重新确认授权后再安排。",
        ),
        ("CHARGE_BLOCKED", "BLOCKED"): ("代扣未执行", "本期商户扣费未通过安全检查，未执行扣款。"),
        ("CHARGE_BLOCKED", "FAILED_INSUFFICIENT"): (
            "代扣未执行",
            "账户余额不足，本期商户扣费未完成。",
        ),
        ("CHARGE_BLOCKED", "SKIPPED_REAPPROVAL"): (
            "代扣需要重新确认",
            "本期商户扣费未执行，请查看授权状态并重新确认。",
        ),
        ("CHARGE_UPCOMING", "SUCCESS"): (
            "您有即将发生的代扣",
            "未来三天有已授权的商户扣费，请查看订阅与账户余额。",
        ),
        ("FUND_RESULT", "CONFIRMED"): (
            "申购已确认",
            "理财申购订单已确认，可在持仓与订单记录中核对。",
        ),
        ("FUND_RESULT", "SETTLED"): (
            "赎回资金已到账",
            "理财赎回订单已结算，可在账户与订单记录中核对。",
        ),
        ("FUND_RESULT", "EXPIRED"): (
            "理财订单已过期",
            "理财订单已过期，请查看订单记录与账户状态。",
        ),
        ("FUND_RESULT", "CONFIRM_PROCESSED"): (
            "理财确认处理已完成",
            "请查看订单记录确认当前状态。",
        ),
        ("FUND_RESULT", "SETTLE_PROCESSED"): ("理财结算处理已完成", "请查看订单记录确认当前状态。"),
        ("FUND_RESULT", "EXPIRY_PROCESSED"): ("理财到期处理已完成", "请查看订单记录确认当前状态。"),
        ("FUND_RESULT", "FAILED"): (
            "理财订单处理未完成",
            "理财订单处理未完成，请查看订单记录与安全记录。",
        ),
    }
)


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
