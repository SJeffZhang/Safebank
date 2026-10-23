"""Approval panel PlanView (PRD §9.5)."""

from __future__ import annotations

from safebank_contracts.base import Contract
from safebank_contracts.enums import Risk
from safebank_contracts.tools import Id

from .decision import PreflightView
from .enums import PlanStatus


class PlanViewField(Contract):
    key: str
    label: str
    value: str
    unit: str | None = None


class PlanViewStep(Contract):
    step_id: Id
    action_kind: str
    title: str
    fields: tuple[PlanViewField, ...] = ()
    irreversible: bool = False


class Disclosure(Contract):
    product_id: Id
    version: str
    text: str


class PlanView(Contract):
    approval_id: Id
    plan_id: Id
    evaluation_id: Id
    session_id: Id
    status: PlanStatus
    origin: str
    risk: Risk
    steps: tuple[PlanViewStep, ...] = ()
    preflight: PreflightView = PreflightView(executable=True)
    findings: tuple[dict, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    risk_ack_required: bool = False
    otp_required: bool = False
    expires_at_wall: str
    view_hash: str
