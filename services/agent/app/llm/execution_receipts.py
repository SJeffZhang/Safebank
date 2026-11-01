"""Deterministic receipts for the current executed plan; never authorize or execute.

Read values may supply an ID-bound display name, never a transaction amount.
Every business fact otherwise comes from its matching validated plan/result pair.
"""

from __future__ import annotations

import re
from decimal import Decimal

from pydantic import ValidationError
from safebank_contracts.plan import ExecutionResult, PlanDraft
from safebank_contracts.views import WRITE_RESULTS

from services.agent.app.graph.state import AgentState

_INCOMPLETE = "操作结果已返回，但回执详情不完整，请查看交易记录或操作记录核对。"
_SAFE_NAME = re.compile(r"[\u4e00-\u9fffA-Za-z· ]{1,32}")
_NAME_CLAIM_MARKERS = (
    "元",
    "块",
    "转账",
    "到账",
    "余额",
    "扣款",
    "操作",
    "忽略",
    "规则",
    "指令",
    "限额",
    "成功",
    "执行",
    "密码",
    "验证码",
)
_WRAPPED_NAME = re.compile(r'<untrusted_data source="[^"<>]+">([^<>\n]+)</untrusted_data>')


def _get(obj: object, key: str, default: object = None) -> object:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _money(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}元"


def _name(state: AgentState, tool: str, collection: str, entity_id: object, field: str) -> str:
    """Return one unambiguous short display value, explicitly used only as data."""
    names: set[str] = set()
    if not isinstance(entity_id, str):
        return ""
    for read in state.get("reads") or ():
        if _get(read, "tool") != tool:
            continue
        items = _get(_get(read, "data"), collection, ()) or ()
        for item in items:
            if _get(item, "id") != entity_id:
                continue
            value = _get(item, field)
            if not isinstance(value, str):
                continue
            wrapped = _WRAPPED_NAME.fullmatch(value)
            if wrapped:
                value = wrapped[1]
            if _SAFE_NAME.fullmatch(value) and not any(
                marker in value for marker in _NAME_CLAIM_MARKERS
            ):
                names.add(value.strip())
    return next(iter(names)) if len(names) == 1 else ""


def _target(state: AgentState, args: object) -> str:
    payee = _get(args, "payee_id")
    if payee is not None:
        name = _name(state, "list_payees", "items", payee, "name")
        return f"收款人“{name}”" if name else "该收款人"
    name = _name(state, "get_accounts", "accounts", _get(args, "to_account_id"), "display_name")
    return f"本人账户“{name}”" if name else "本人目标账户"


def _tail(last4: object) -> str:
    return (
        f"尾号{last4}的卡"
        if isinstance(last4, str) and re.fullmatch(r"[0-9]{4}", last4)
        else "该卡"
    )


def _card_label(state: AgentState, card_id: str) -> str:
    tails: set[str] = set()
    for read in state.get("reads") or ():
        if _get(read, "tool") != "get_cards":
            continue
        for item in _get(_get(read, "data"), "items", ()) or ():
            if _get(item, "id") == card_id:
                tail = _get(item, "last4")
                if isinstance(tail, str) and re.fullmatch(r"[0-9]{4}", tail):
                    tails.add(tail)
    return _tail(next(iter(tails))) if len(tails) == 1 else "该卡"


def _fund_receipt(tool: str, args: object, result: object) -> str | None:
    order = result.order
    expected_type = "SUB" if tool == "subscribe_product" else "RED"
    if (order.account_id, order.product_id, order.type) != (
        args.account_id,
        args.product_id,
        expected_type,
    ):
        return None
    if tool == "subscribe_product" and order.amount_cents != args.amount_cents:
        return None
    if tool == "redeem_product" and Decimal(order.units) != Decimal(args.units):
        return None
    action = "申购" if tool == "subscribe_product" else "赎回"
    if order.status not in ("PENDING_CONFIRM", "PENDING_SETTLEMENT", "SETTLED"):
        return f"{action}订单未完成，请查看订单记录核对。"
    pending = order.status != "SETTLED"
    status = {
        "PENDING_CONFIRM": "已提交，待确认",
        "PENDING_SETTLEMENT": "已提交，待结算",
        "SETTLED": "已结算",
    }[order.status]
    if tool == "subscribe_product":
        detail = f"金额{_money(order.amount_cents)}"
    else:
        amount_label = "预计金额" if pending else "金额"
        detail = f"份额{order.units}份，{amount_label}{_money(order.amount_cents)}"
    return f"{action}订单{status}，{detail}。"


def _step_receipt(state: AgentState, step: object, result: object) -> str | None:
    tool, args = step.tool, step.args
    if tool == "transfer":
        return (
            f"已向{_target(state, args)}转账{_money(args.amount_cents)}；"
            f"付款账户执行后可用余额{_money(result.from_available_after_cents)}。"
        )
    if tool == "add_payee":
        return "该账号已存在于您的收款人列表中，未重复添加。" if result.no_op else "收款人已添加。"
    if tool == "schedule_transfer":
        return (
            f"定时转账已创建，向{_target(state, args)}单次转账{_money(args.amount_cents)}；"
            "尚未执行扣款。"
        )
    if tool in ("cancel_scheduled", "cancel_subscription"):
        if result.instruction_id != args.instruction_id:
            return None
        label = "定时转账" if tool == "cancel_scheduled" else "订阅代扣"
        if result.no_op:
            return f"{label}此前已取消，本次未变更。"
        if tool == "cancel_subscription":
            return (
                f"{label}已取消；预计每月节省{_money(result.monthly_saving_cents)}，"
                f"每年节省{_money(result.annual_saving_cents)}。"
            )
        return f"{label}已取消。"
    if tool == "create_split_request":
        if result.account_id != args.account_id or result.total_cents != args.total_cents:
            return None
        return (
            f"AA收款请求已创建，总额{_money(result.total_cents)}；"
            f"已收{_money(result.received_cents)}，待收{_money(result.outstanding_cents)}。"
        )
    if tool in ("freeze_card", "unfreeze_card"):
        if result.card_id != args.card_id:
            return None
        label = _card_label(state, args.card_id)
        action = "冻结" if tool == "freeze_card" else "解冻"
        return f"{label}此前已{action}，本次未变更。" if result.no_op else f"{label}已{action}。"
    if tool == "report_card_lost":
        if result.old_card_id != args.card_id:
            return None
        text = f"{_card_label(state, args.card_id)}已挂失。"
        if result.new_card_id is not None:
            text += f"已生成补卡，{_tail(result.new_last4)}。"
        return text
    if tool in ("update_card_limits", "update_card_controls"):
        card = result.card
        if card.id != args.card_id:
            return None
        if tool == "update_card_limits":
            return (
                f"{_tail(card.last4)}限额已更新；单笔限额{_money(card.single_limit_cents)}，"
                f"每日限额{_money(card.daily_limit_cents)}。"
            )
        labels = (("online", "线上"), ("overseas", "境外"), ("atm", "ATM"), ("quickpass", "闪付"))
        settings = "、".join(
            f"{label}{'开启' if getattr(card.controls, key) else '关闭'}" for key, label in labels
        )
        return f"{_tail(card.last4)}用途设置已更新；{settings}。"
    if tool == "submit_risk_assessment":
        if not 1 <= result.level <= 5:
            return None
        return f"风险测评已提交，测评等级R{result.level}。"
    if tool in ("subscribe_product", "redeem_product"):
        return _fund_receipt(tool, args, result)
    return None


def execution_receipt(state: AgentState) -> str:
    """Fail closed unless the entire execution matches the current validated draft."""
    try:
        execution = ExecutionResult.model_validate(state.get("execution"))
        draft = PlanDraft.model_validate(state.get("plan_draft"))
        plan_id = state.get("plan_id")
        decision = state.get("decision")
        if (
            execution.status != "EXECUTED"
            or execution.committed_at is None
            or execution.error_code is not None
            or not plan_id
            or execution.plan_id != plan_id
            or _get(decision, "plan_id") != plan_id
            or _get(decision, "decision") not in ("ALLOW", "REQUIRE_APPROVAL")
            or not state.get("session_id")
            or draft.session_id != state["session_id"]
        ):
            return _INCOMPLETE
        results = {item.step_id: item for item in execution.steps}
        if len(results) != len(execution.steps) or set(results) != {
            step.step_id for step in draft.steps
        }:
            return _INCOMPLETE
        lines: list[str] = []
        for step in draft.steps:
            item = results[step.step_id]
            if item.tool != step.tool or item.status != "EXECUTED" or item.code is not None:
                return _INCOMPLETE
            result = WRITE_RESULTS[step.tool].model_validate(item.result)
            text = _step_receipt(state, step, result)
            if not text:
                return _INCOMPLETE
            lines.append(text)
        return (
            "\n".join(f"第{i}步：{line}" for i, line in enumerate(lines, 1))
            if len(lines) > 1
            else lines[0]
        )
    except (ValidationError, TypeError, ValueError, KeyError, AttributeError):
        return _INCOMPLETE
