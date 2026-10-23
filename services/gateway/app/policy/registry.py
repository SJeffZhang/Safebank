"""Tool policy registry (PRD §9.4 / §7.6).

W0 pins the structure and fills the 14 write tools. W2 may only submit value
corrections; changing a field here is an interface change for the conductor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from safebank_contracts.tools import WRITE_ARGS

from ..schemas.enums import ActionKind

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "policies" / "tools.yaml"

_CARD_LIMIT_FIELDS = ("single_limit_cents", "daily_limit_cents")
_CARD_CONTROL_FIELDS = ("online", "overseas", "atm", "quickpass")


@dataclass(frozen=True)
class ToolPolicy:
    tool: str
    base_risk: str
    sensitive_params: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    outflow: bool
    irreversible: bool
    otp_required: bool
    title_template: str


def load_policies(path: str | Path | None = None) -> dict[str, ToolPolicy]:
    raw = yaml.safe_load((Path(path) if path else DEFAULT_PATH).read_text())
    policies = {
        item["tool"]: ToolPolicy(
            tool=item["tool"],
            base_risk=item["base_risk"],
            sensitive_params=tuple(item["sensitive_params"]),
            allowed_origins=tuple(item["allowed_origins"]),
            outflow=bool(item["outflow"]),
            irreversible=bool(item["irreversible"]),
            otp_required=bool(item["otp_required"]),
            title_template=item["title_template"],
        )
        for item in raw
    }
    expected = set(WRITE_ARGS)
    if set(policies) != expected:
        missing = expected - set(policies)
        extra = set(policies) - expected
        raise ValueError(
            f"tools.yaml tool set mismatch: missing={sorted(missing)} extra={sorted(extra)}"
        )
    for policy in policies.values():
        if policy.base_risk not in ("GREEN", "YELLOW", "RED"):
            raise ValueError(f"{policy.tool}: bad base_risk {policy.base_risk!r}")
    return policies


POLICIES = load_policies()


def policy(tool: str) -> ToolPolicy:
    try:
        return POLICIES[tool]
    except KeyError as exc:
        raise ValueError(f"unknown tool {tool!r}") from exc


def _as_dict(args: Any) -> dict[str, Any]:
    return args if isinstance(args, dict) else args.model_dump(mode="json")


def _card(shadow_state: Any, card_id: str) -> Any:
    cards = getattr(shadow_state, "cards", None)
    if cards is None:
        return None
    return cards.get(card_id)


def action_kind(tool: str, args: Any, shadow_state: Any) -> ActionKind:
    """Map a tool + its args to an ActionKind (PRD §7.6).

    Card tools compare the new values against ``shadow_state`` (a ``BankState``
    or anything exposing ``.cards``) to decide TIGHTEN vs LOOSEN: any increase
    of a limit, or any channel turned on, is a LOOSEN.
    """
    values = _as_dict(args)
    if tool == "transfer":
        return (
            ActionKind.TRANSFER_SELF
            if values.get("to_account_id")
            else ActionKind.TRANSFER_EXTERNAL
        )
    if tool == "update_card_limits":
        current = _card(shadow_state, values.get("card_id"))
        loosened = any(
            values.get(field) is not None and values[field] > getattr(current, field)
            for field in _CARD_LIMIT_FIELDS
            if current is not None
        )
        return ActionKind.CARD_LIMIT_LOOSEN if loosened else ActionKind.CARD_LIMIT_TIGHTEN
    if tool == "update_card_controls":
        current = _card(shadow_state, values.get("card_id"))
        if current is not None:
            controls = current.controls
            loosened = any(
                values.get(field) is True and getattr(controls, field) is False
                for field in _CARD_CONTROL_FIELDS
            )
            return ActionKind.CARD_CONTROL_LOOSEN if loosened else ActionKind.CARD_CONTROL_TIGHTEN
        return ActionKind.CARD_CONTROL_TIGHTEN
    return {
        "add_payee": ActionKind.PAYEE_ADD,
        "schedule_transfer": ActionKind.SCHEDULE_CREATE,
        "cancel_scheduled": ActionKind.SCHEDULE_CANCEL,
        "create_split_request": ActionKind.SPLIT_CREATE,
        "freeze_card": ActionKind.CARD_FREEZE,
        "unfreeze_card": ActionKind.CARD_UNFREEZE,
        "report_card_lost": ActionKind.CARD_LOST,
        "cancel_subscription": ActionKind.MANDATE_CANCEL,
        "submit_risk_assessment": ActionKind.ASSESSMENT_SUBMIT,
        "subscribe_product": ActionKind.FUND_SUBSCRIBE,
        "redeem_product": ActionKind.FUND_REDEEM,
    }[tool]
