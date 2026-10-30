"""L3 internal event endpoints (PRD §7.11, §8.5).

The scheduler (or the gateway on its behalf) drives due ``event_jobs`` through
three internal endpoints: read the immutable event definition, execute the due
period, and record a terminal non-success block. All run under the service
token applied globally in :mod:`app.main`.

Locking follows :class:`app.execution.ExecutionService`: customer existence
check → ``SET LOCAL lock_timeout`` → event job row ``FOR UPDATE`` → clock
``FOR UPDATE`` (read) → ``state_versions`` ``FOR UPDATE`` → version/clock
comparison (409 ``STATE_CHANGED``) → ``load_bank_state`` → bankcore applier →
``persist`` → ``instruction_runs`` row → terminal ``event_executions`` row →
mark the job ``DONE``. The job row itself is the per-event serialization point,
so two concurrent executions of the same event cannot both post.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from safebank_bankcore import events as bank_events
from safebank_bankcore import external as bank_external
from safebank_bankcore import funds as bank_funds
from safebank_bankcore.banking import Context
from safebank_bankcore.grants import instruction_scope
from safebank_contracts.codes import Code
from safebank_contracts.obligations import InstructionRun
from safebank_contracts.state import StateScope
from sqlalchemy import select, text

from . import models as m
from .errors import BankError
from .state import load_bank_state, persist


class ExecuteEventRequest(BaseModel):
    expected_versions: dict[str, int]
    clock_version: int
    expected_payload_hash: str
    authorization_evidence: dict = Field(default_factory=dict)


class BlockEventRequest(BaseModel):
    status: str
    code: str | None = None
    expected_versions: dict[str, int]
    clock_version: int
    expected_payload_hash: str


_BLOCK_STATUSES = ("BLOCKED", "SKIPPED_REAPPROVAL", "FAILED_INSUFFICIENT")
_FUND_KINDS = ("FUND_CONFIRM", "FUND_SETTLE", "FUND_EXPIRY")


def utcnow() -> datetime:
    return datetime.now(ZoneInfo("UTC"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _demo_data(immutable_payload: object) -> dict:
    raw = (
        immutable_payload.get("payload")
        if isinstance(immutable_payload, dict)
        else immutable_payload
    )
    return json.loads(raw) if isinstance(raw, str) else raw


def _execution_dict(row) -> dict:
    return {
        "status": row["status"],
        "authorization_evidence": row["authorization_evidence"],
        "result": row["result_json"],
        "committed_at": _iso(row["committed_at"]),
    }


class EventService:
    """Internal event endpoints (read / execute / block)."""

    def __init__(self, *, wall_clock=utcnow) -> None:
        self._wall_clock = wall_clock

    async def _persist_period_cursor(self, conn, state, run) -> None:
        """Persist the pure applier's source cursor in the event transaction.

        Ledger effects do not carry obligation snapshot updates. Only the run's
        own source is updated, with the pure applier's date, never caller input.
        """
        if not isinstance(run, InstructionRun):
            return
        if run.billing_id is not None:
            billing = state.obligations.billings[run.billing_id]
            await conn.execute(
                m.merchant_billing.update()
                .where(m.merchant_billing.c.id == run.billing_id)
                .values(next_due_at=billing.next_due_at)
            )
        else:
            instruction = state.obligations.standing_instructions[run.instruction_id]
            await conn.execute(
                m.standing_instructions.update()
                .where(m.standing_instructions.c.id == run.instruction_id)
                .values(next_run_at=instruction.next_run_at)
            )

    async def _job_for_update(self, conn, event_id: str):
        return (
            (
                await conn.execute(
                    select(m.event_jobs).where(m.event_jobs.c.id == event_id).with_for_update()
                )
            )
            .mappings()
            .first()
        )

    async def _execution(self, conn, event_id: str):
        return (
            (
                await conn.execute(
                    select(m.event_executions).where(m.event_executions.c.event_id == event_id)
                )
            )
            .mappings()
            .first()
        )

    async def _customer_for(self, conn, row) -> str | None:
        kind = row["kind"]
        source_id = row["source_id"]
        if kind == "SCHEDULED_TRANSFER":
            customer = (
                await conn.execute(
                    select(m.standing_instructions.c.customer_id).where(
                        m.standing_instructions.c.id == source_id
                    )
                )
            ).scalar()
        elif kind == "MERCHANT_CHARGE":
            customer = (
                await conn.execute(
                    select(m.merchant_billing.c.customer_id).where(
                        m.merchant_billing.c.id == source_id
                    )
                )
            ).scalar()
        elif kind == "DEMO_EVENT":
            customer = _demo_data(row["immutable_payload"]).get("customer_id")
        elif kind in _FUND_KINDS:
            customer = (
                await conn.execute(
                    select(m.fund_orders.c.customer_id).where(m.fund_orders.c.id == source_id)
                )
            ).scalar()
        else:
            customer = None
        if customer is None and kind in ("SCHEDULED_TRANSFER", "MERCHANT_CHARGE"):
            raise BankError(Code.NOT_FOUND, status=404)
        return customer

    async def _fund_order_ctx(self, conn, order_id: str):
        """Load fund order fields needed to build a bankcore Context."""
        row = (
            (
                await conn.execute(
                    select(
                        m.fund_orders.c.customer_id,
                        m.fund_orders.c.plan_id,
                        m.fund_orders.c.step_id,
                    ).where(m.fund_orders.c.id == order_id)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise BankError(Code.NOT_FOUND, status=404)
        return row

    async def _execute_fund_event(
        self,
        conn,
        row,
        event_id: str,
        expected_versions: dict,
        clock_version: int,
        expected_payload_hash: str,
        authorization_evidence: dict,
    ) -> dict:
        """Execute a FUND_CONFIRM / FUND_SETTLE / FUND_EXPIRY event.

        Fund events map to bankcore fund lifecycle functions.  There is no
        InstructionRun row; the domain effects (UpdateFundOrder, SetFundHolding,
        ledger transfers) are persisted directly and the terminal
        event_executions row records success or failure.
        """
        order_id = row["source_id"]
        order_ctx = await self._fund_order_ctx(conn, order_id)
        customer_id = order_ctx["customer_id"]
        await self._check_customer(conn, customer_id)
        version = await self._lock_clock_and_version(
            conn, customer_id, clock_version, expected_versions
        )
        due_at = row["due_at"]
        state = await load_bank_state(
            conn,
            StateScope(customer_ids=(customer_id,)),
            due_at,
            customer_id=customer_id,
        )
        ctx = Context(
            customer_id=customer_id,
            plan_id=order_ctx["plan_id"],
            step_id=order_ctx["step_id"],
        )
        kind = row["kind"]
        if kind == "FUND_CONFIRM":
            state, effects, errors = bank_funds.apply_fund_confirm(
                state,
                order_id=order_id,
                at=due_at,
                ctx=ctx,
            )
        elif kind == "FUND_SETTLE":
            state, effects, errors = bank_funds.apply_fund_settle(
                state,
                order_id=order_id,
                at=due_at,
                ctx=ctx,
            )
        else:  # FUND_EXPIRY
            state, effects, errors = bank_funds.apply_pending_expiry(
                state,
                order_id=order_id,
                at=due_at,
                ctx=ctx,
            )
        await persist(conn, effects)
        await self._bump_version(conn, customer_id, version["version"] + 1)
        committed = self._wall_clock()
        if errors:
            result = {"status": "FAILED", "code": errors[0].code.value}
            exec_status = "FAILED_INSUFFICIENT"
        else:
            result = {"status": "SUCCESS"}
            exec_status = "SUCCESS"
        await conn.execute(
            m.event_executions.insert().values(
                event_id=event_id,
                status=exec_status,
                authorization_evidence=authorization_evidence or {},
                result_json=result,
                committed_at=committed,
            )
        )
        await conn.execute(
            m.event_jobs.update().where(m.event_jobs.c.id == event_id).values(status="DONE")
        )
        return {
            "event_id": event_id,
            "customer_id": customer_id,
            "status": exec_status,
            "result": result,
            "committed_at": committed.isoformat(),
        }

    async def _check_customer(self, conn, customer_id: str) -> None:
        found = (
            await conn.execute(select(m.customers.c.id).where(m.customers.c.id == customer_id))
        ).scalar()
        if found is None:
            raise BankError(Code.NOT_FOUND, status=404)

    async def _lock_clock_and_version(self, conn, customer_id, clock_version, expected_versions):
        clock = (
            (await conn.execute(select(m.clock).where(m.clock.c.id == 1).with_for_update()))
            .mappings()
            .first()
        )
        if clock is None or clock["version"] != clock_version:
            raise BankError(Code.STATE_CHANGED)
        version = (
            (
                await conn.execute(
                    select(m.state_versions)
                    .where(m.state_versions.c.customer_id == customer_id)
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if version is None or expected_versions.get(customer_id) != version["version"]:
            raise BankError(Code.STATE_CHANGED)
        return version

    async def _bump_version(self, conn, customer_id: str, version: int) -> None:
        await conn.execute(
            m.state_versions.update()
            .where(m.state_versions.c.customer_id == customer_id)
            .values(version=version)
        )

    async def get_event(self, conn, event_id: str, customer_id: str | None) -> dict:
        row = (
            (await conn.execute(select(m.event_jobs).where(m.event_jobs.c.id == event_id)))
            .mappings()
            .first()
        )
        if row is None:
            raise BankError(Code.NOT_FOUND, status=404)
        owner = await self._customer_for(conn, row)
        if customer_id is not None and owner != customer_id:
            raise BankError(Code.NOT_FOUND, status=404)
        execution = await self._execution(conn, event_id)
        payload = row["immutable_payload"]
        # Associated definitions are authoritative DB rows, never callback fields.
        # Version fencing on execute/block prevents using a definition changed
        # after this read. Merchant source_id is a BILLING, not a mandate ID.
        definition = {}
        instruction = None
        if row["kind"] == "SCHEDULED_TRANSFER":
            instruction = (
                (
                    await conn.execute(
                        select(m.standing_instructions).where(
                            m.standing_instructions.c.id == row["source_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            definition = {
                "account_id": instruction["from_account_id"],
                "payee_id": instruction["payee_id"],
                "to_account_id": instruction["to_account_id"],
                "amount_cents": instruction["amount_cents"],
            }
        elif row["kind"] == "MERCHANT_CHARGE":
            billing = (
                (
                    await conn.execute(
                        select(m.merchant_billing).where(
                            m.merchant_billing.c.id == row["source_id"]
                        )
                    )
                )
                .mappings()
                .one()
            )
            instruction = (
                (
                    await conn.execute(
                        select(m.standing_instructions).where(
                            m.standing_instructions.c.billing_id == row["source_id"],
                            m.standing_instructions.c.customer_id == owner,
                            m.standing_instructions.c.kind == "MERCHANT_MANDATE",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            definition = {
                "account_id": billing["account_id"],
                "amount_cents": billing["amount_cents"],
                "billing_id": billing["id"],
                "merchant_id": billing["merchant_id"],
            }
        if instruction is not None:
            definition["grant_scope"] = instruction_scope(
                instruction, billing if row["kind"] == "MERCHANT_CHARGE" else None
            )
            source = (
                (
                    await conn.execute(
                        select(m.plan_executions).where(
                            m.plan_executions.c.customer_id == owner,
                            m.plan_executions.c.plan_id == instruction["source_plan_id"],
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            # Approval risk comes from actual committed evidence, never a default.
            if source and source["result_json"].get("status") == "EXECUTED":
                definition["source_approved_risk"] = source["authorization_evidence"].get("risk")
                operations = (
                    await conn.execute(
                        select(m.operation_events.c.payload).where(
                            m.operation_events.c.customer_id == owner,
                            m.operation_events.c.plan_id == instruction["source_plan_id"],
                            m.operation_events.c.action_kind == "SCHEDULED_TRANSFER_CREATE",
                        )
                    )
                ).scalars()
                originals = [
                    p["instruction"]
                    for p in operations
                    if (p.get("instruction") or {}).get("id") == instruction["id"]
                ]
                if len(originals) == 1:
                    definition["source_instruction"] = originals[0]
        if row["kind"] in ("SCHEDULED_TRANSFER", "MERCHANT_CHARGE"):
            definition.update(
                {
                    "instruction_id": instruction["id"] if instruction else None,
                    "source_plan_id": instruction["source_plan_id"] if instruction else None,
                    "grant_scope_hash": instruction["grant_scope_hash"] if instruction else None,
                }
            )
        return {
            **definition,
            "event_id": row["id"],
            "kind": row["kind"],
            "customer_id": owner,
            "due_at": _iso(row["due_at"]),
            "payload": payload.get("payload") if isinstance(payload, dict) else payload,
            "payload_hash": row["payload_hash"],
            "source_id": row["source_id"],
            "period_key": row["period_key"],
            "job_status": row["status"],
            "execution": _execution_dict(execution) if execution is not None else None,
        }

    async def execute_event(
        self,
        conn,
        event_id: str,
        expected_versions: dict,
        clock_version: int,
        expected_payload_hash: str,
        authorization_evidence: dict,
    ) -> dict:
        await conn.execute(text("SET LOCAL lock_timeout = '3s'"))
        row = await self._job_for_update(conn, event_id)
        if row is None:
            raise BankError(Code.NOT_FOUND, status=404)
        execution = await self._execution(conn, event_id)
        if execution is not None:
            # Terminal row already recorded: return it unchanged, do not execute.
            return _execution_dict(execution) | {"event_id": event_id, "replayed": True}
        if expected_payload_hash != row["payload_hash"]:
            raise BankError(Code.CONTRACT_INVALID, status=422)
        kind = row["kind"]
        if kind in _FUND_KINDS:
            return await self._execute_fund_event(
                conn,
                row,
                event_id,
                expected_versions,
                clock_version,
                expected_payload_hash,
                authorization_evidence,
            )
        customer_id = await self._customer_for(conn, row)
        await self._check_customer(conn, customer_id)
        version = await self._lock_clock_and_version(
            conn, customer_id, clock_version, expected_versions
        )
        state = await load_bank_state(
            conn,
            StateScope(customer_ids=(customer_id,)),
            row["due_at"],
            customer_id=customer_id,
        )
        if kind == "SCHEDULED_TRANSFER":
            state, effects, run = bank_events.apply_scheduled_transfer(
                state,
                instruction_id=row["source_id"],
                due_at=row["due_at"],
                event_id=event_id,
                period_key=row["period_key"],
            )
        elif kind == "MERCHANT_CHARGE":
            state, effects, run = bank_events.apply_merchant_charge(
                state,
                billing_id=row["source_id"],
                due_at=row["due_at"],
                event_id=event_id,
                period_key=row["period_key"],
            )
        else:  # DEMO_EVENT
            state, effects, run = self._apply_demo(state, row)
        await persist(conn, effects)
        await self._persist_period_cursor(conn, state, run)
        await self._bump_version(conn, customer_id, version["version"] + 1)
        if isinstance(run, InstructionRun):
            await conn.execute(m.instruction_runs.insert().values(**run.model_dump(mode="python")))
            result = {
                "status": run.status,
                "code": run.code,
                "transfer_id": run.transfer_id,
                "amount_cents": run.amount_cents,
            }
        else:
            result = {}
        event_status = run.status if isinstance(run, InstructionRun) else "SUCCESS"
        committed = self._wall_clock()
        await conn.execute(
            m.event_executions.insert().values(
                event_id=event_id,
                status=event_status,
                authorization_evidence=authorization_evidence or {},
                result_json=result,
                committed_at=committed,
            )
        )
        await conn.execute(
            m.event_jobs.update().where(m.event_jobs.c.id == event_id).values(status="DONE")
        )
        return {
            "event_id": event_id,
            "customer_id": customer_id,
            "status": event_status,
            "result": result,
            "committed_at": committed.isoformat(),
        }

    async def block_event(
        self,
        conn,
        event_id: str,
        status: str,
        code: str,
        expected_versions: dict,
        clock_version: int,
        expected_payload_hash: str,
    ) -> dict:
        if status not in _BLOCK_STATUSES:
            raise BankError(Code.CONTRACT_INVALID, status=422)
        await conn.execute(text("SET LOCAL lock_timeout = '3s'"))
        row = await self._job_for_update(conn, event_id)
        if row is None:
            raise BankError(Code.NOT_FOUND, status=404)
        execution = await self._execution(conn, event_id)
        if execution is not None:
            # Never overwrite an existing terminal row (including SUCCESS).
            return _execution_dict(execution) | {"event_id": event_id, "replayed": True}
        if expected_payload_hash != row["payload_hash"]:
            raise BankError(Code.CONTRACT_INVALID, status=422)
        kind = row["kind"]
        if kind not in ("SCHEDULED_TRANSFER", "MERCHANT_CHARGE"):
            raise BankError(Code.TOOL_NOT_ALLOWED, status=422)
        customer_id = await self._customer_for(conn, row)
        await self._check_customer(conn, customer_id)
        version = await self._lock_clock_and_version(
            conn, customer_id, clock_version, expected_versions
        )
        state = await load_bank_state(
            conn,
            StateScope(customer_ids=(customer_id,)),
            row["due_at"],
            customer_id=customer_id,
        )
        if kind == "SCHEDULED_TRANSFER":
            state, effects, run = bank_events.block_instruction(
                state,
                instruction_id=row["source_id"],
                due_at=row["due_at"],
                event_id=event_id,
                period_key=row["period_key"],
                status=status,
                code=code,
            )
        else:
            state, effects, run = bank_events.block_billing(
                state,
                billing_id=row["source_id"],
                due_at=row["due_at"],
                event_id=event_id,
                period_key=row["period_key"],
                status=status,
                code=code,
            )
        await persist(conn, effects)
        await self._persist_period_cursor(conn, state, run)
        await self._bump_version(conn, customer_id, version["version"] + 1)
        if isinstance(run, InstructionRun):
            await conn.execute(m.instruction_runs.insert().values(**run.model_dump(mode="python")))
            result = {
                "status": run.status,
                "code": run.code,
                "transfer_id": run.transfer_id,
                "amount_cents": run.amount_cents,
            }
        else:
            result = {}
        committed = self._wall_clock()
        await conn.execute(
            m.event_executions.insert().values(
                event_id=event_id,
                status=status,
                authorization_evidence={},
                result_json=result,
                committed_at=committed,
            )
        )
        await conn.execute(
            m.event_jobs.update().where(m.event_jobs.c.id == event_id).values(status="DONE")
        )
        return {
            "event_id": event_id,
            "customer_id": customer_id,
            "status": status,
            "result": result,
            "committed_at": committed.isoformat(),
        }

    def _apply_demo(self, state, row):
        data = _demo_data(row["immutable_payload"])
        kind = data.get("kind")
        ctx = Context(
            customer_id=data.get("customer_id"),
            plan_id="system",
            step_id="event",
        )
        now = row["due_at"]
        if kind == "credit":
            args = bank_external.ExternalCredit(
                account_id=data["account_id"],
                amount_cents=data["amount_cents"],
                counterparty=data.get("counterparty"),
                memo=data.get("memo"),
                kind=data.get("tx_kind", "SALARY"),
            )
            return bank_external.apply_external_credit(state, args, now, ctx)
        if kind == "debit":
            args = bank_external.ExternalDebit(
                account_id=data["account_id"],
                amount_cents=data["amount_cents"],
                counterparty=data.get("counterparty"),
                memo=data.get("memo"),
                kind=data.get("tx_kind", "CARD_SPEND"),
            )
            return bank_external.apply_external_debit(state, args, now, ctx)
        if kind == "split":
            args = bank_external.SplitPayment(
                split_id=data["split_id"],
                item_id=data["item_id"],
                amount_cents=data["amount_cents"],
            )
            return bank_external.apply_split_payment(state, args, now, ctx)
        raise BankError(Code.TOOL_NOT_ALLOWED, status=422)
