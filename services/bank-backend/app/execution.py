"""PRD §8.4/8.5 atomic plan execution and durable cancellation fences."""

import asyncio
from datetime import UTC, datetime

from safebank_bankcore.banking import apply_plan
from safebank_bankcore.ids import digest
from safebank_bankcore.references import request_digest, resolved_steps
from safebank_contracts.codes import Code
from safebank_contracts.plan import ExecutionResult
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from . import models as m
from .errors import BankError
from .state import load_bank_state, persist, resolve_scope


def utcnow():
    return datetime.now(UTC)


def steps_digest(steps):
    return request_digest(steps)


class ExecutionService:
    def __init__(self, db, fingerprint_key: str, *, wall_clock=utcnow, before_commit=None):
        self.db = db
        self.fingerprint_key = fingerprint_key
        self.wall_clock = wall_clock
        self.before_commit = before_commit  # trusted Python test hook, never a request parameter

    async def customer(self, conn, customer_id):
        if (
            await conn.scalar(select(m.customers.c.id).where(m.customers.c.id == customer_id))
            is None
        ):
            raise BankError(Code.NOT_FOUND, 404)

    async def intent(self, conn, customer_id, plan_id):
        await conn.execute(text("SET LOCAL lock_timeout='3s'"))
        return (
            (
                await conn.execute(
                    select(m.execution_intents)
                    .where(
                        m.execution_intents.c.customer_id == customer_id,
                        m.execution_intents.c.plan_id == plan_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )

    def verify_identity(self, row, command):
        if row["plan_hash"] != command.plan_hash or row["request_digest"] != steps_digest(
            command.steps
        ):
            raise BankError(Code.IDEMPOTENCY_CONFLICT)

    async def prepare(self, command):
        now = self.wall_clock()
        async with self.db.begin() as conn:
            await self.customer(conn, command.customer_id)
            row = await self.intent(conn, command.customer_id, command.plan_id)
            evidence = command.authorization_evidence.model_dump(mode="json")
            if row is None:
                if command.attempt != 1:
                    raise BankError(Code.IDEMPOTENCY_CONFLICT)
                if command.deadline_wall <= now or command.deadline_cap_wall <= now:
                    raise BankError(Code.EXECUTION_DEADLINE_EXPIRED)
                scope = await resolve_scope(
                    conn, command.customer_id, command.steps, self.fingerprint_key
                )
                clock = (await conn.execute(select(m.clock))).mappings().one()
                state = await load_bank_state(
                    conn, scope, clock["sim_now"], customer_id=command.customer_id
                )
                candidate = dict(
                    customer_id=command.customer_id,
                    plan_id=command.plan_id,
                    attempt=command.attempt,
                    plan_hash=command.plan_hash,
                    steps_digest=digest(
                        resolved_steps(state, command.steps, command.customer_id, command.plan_id)
                    ),
                    request_digest=steps_digest(command.steps),
                    authorization_evidence=evidence,
                    evidence_history=[],
                    status="PREPARED",
                    deadline_wall=command.deadline_wall,
                    deadline_cap_wall=command.deadline_cap_wall,
                    created_at_wall=now,
                )
                await conn.execute(
                    insert(m.execution_intents).values(**candidate).on_conflict_do_nothing()
                )
                row = await self.intent(conn, command.customer_id, command.plan_id)
            if row["status"] == "CANCELLED":
                raise BankError(Code.EXECUTION_CANCELLED)
            self.verify_identity(row, command)
            if command.deadline_cap_wall != row["deadline_cap_wall"]:
                raise BankError(Code.IDEMPOTENCY_CONFLICT)
            if command.attempt == row["attempt"]:
                if evidence != row["authorization_evidence"]:
                    raise BankError(Code.IDEMPOTENCY_CONFLICT)
                if row["status"] == "PREPARED":
                    await conn.execute(
                        update(m.execution_intents)
                        .where(
                            m.execution_intents.c.customer_id == command.customer_id,
                            m.execution_intents.c.plan_id == command.plan_id,
                        )
                        .values(deadline_wall=min(row["deadline_wall"], command.deadline_wall))
                    )
            elif command.attempt == row["attempt"] + 1 and row["status"] == "PREPARED":
                if (
                    command.deadline_wall <= self.wall_clock()
                    or command.deadline_wall > row["deadline_cap_wall"]
                ):
                    raise BankError(Code.EXECUTION_DEADLINE_EXPIRED)
                history = [
                    *row["evidence_history"],
                    {
                        "attempt": row["attempt"],
                        "authorization_evidence": row["authorization_evidence"],
                        "deadline_wall": row["deadline_wall"].isoformat(),
                    },
                ]
                await conn.execute(
                    update(m.execution_intents)
                    .where(
                        m.execution_intents.c.customer_id == command.customer_id,
                        m.execution_intents.c.plan_id == command.plan_id,
                    )
                    .values(
                        attempt=command.attempt,
                        authorization_evidence=evidence,
                        evidence_history=history,
                        deadline_wall=command.deadline_wall,
                    )
                )
            else:
                raise BankError(Code.IDEMPOTENCY_CONFLICT)
            return {"status": row["status"], "attempt": command.attempt}

    async def execute(self, command):
        for retry in range(3):
            try:
                return await self._execute(command)
            except DBAPIError as exc:
                code = getattr(exc.orig, "sqlstate", None)
                if code in ("40P01", "40001") and retry < 2:
                    await asyncio.sleep(0.01 * (retry + 1))
                    continue
                if code == "55P03":
                    raise BankError(Code.EXECUTION_IN_PROGRESS) from exc
                raise

    async def _execute(self, command):
        async with self.db.begin() as conn:
            row = await self.intent(conn, command.customer_id, command.plan_id)
            if row is None:
                raise BankError(Code.NOT_FOUND, 404)
            if row["status"] == "CANCELLED":
                raise BankError(Code.EXECUTION_CANCELLED)
            self.verify_identity(row, command)
            evidence = command.authorization_evidence.model_dump(mode="json")
            if row["attempt"] != command.attempt or row["authorization_evidence"] != evidence:
                raise BankError(Code.IDEMPOTENCY_CONFLICT)
            if row["status"] == "COMMITTED":
                result = (
                    await conn.execute(
                        select(m.plan_executions.c.result_json).where(
                            m.plan_executions.c.customer_id == command.customer_id,
                            m.plan_executions.c.plan_id == command.plan_id,
                        )
                    )
                ).scalar_one()
                return ExecutionResult.model_validate(result).model_copy(update={"replayed": True})
            if self.wall_clock() >= row["deadline_wall"]:
                raise BankError(Code.EXECUTION_DEADLINE_EXPIRED)
            clock = (
                (
                    await conn.execute(
                        select(m.clock).where(m.clock.c.id == 1).with_for_update(read=True)
                    )
                )
                .mappings()
                .one()
            )
            scope = await resolve_scope(
                conn, command.customer_id, command.steps, self.fingerprint_key
            )
            versions = dict(
                (
                    await conn.execute(
                        select(m.state_versions.c.customer_id, m.state_versions.c.version)
                        .where(m.state_versions.c.customer_id.in_(scope.customer_ids))
                        .order_by(m.state_versions.c.customer_id)
                        .with_for_update()
                    )
                ).all()
            )
            if versions != command.expected_versions or clock["version"] != command.clock_version:
                raise BankError(Code.STATE_CHANGED)
            checked = await resolve_scope(
                conn, command.customer_id, command.steps, self.fingerprint_key
            )
            if scope != checked:
                raise BankError(Code.STATE_CHANGED)
            if self.wall_clock() >= row["deadline_wall"]:
                raise BankError(Code.EXECUTION_DEADLINE_EXPIRED)
            state = await load_bank_state(
                conn, scope, clock["sim_now"], customer_id=command.customer_id
            )
            current_digest = digest(
                resolved_steps(state, command.steps, command.customer_id, command.plan_id)
            )
            if current_digest != row["steps_digest"]:
                raise BankError(Code.STATE_CHANGED)
            simulation = apply_plan(state, command.steps, command.customer_id, command.plan_id)
            if simulation.error:
                return ExecutionResult(
                    plan_id=command.plan_id,
                    status="FAILED",
                    steps=simulation.results,
                    versions=versions,
                    committed_at=None,
                    error_code=simulation.error,
                    replayed=False,
                )
            await persist(conn, simulation.effects)
            for customer_id in sorted(versions):
                await conn.execute(
                    update(m.state_versions)
                    .where(m.state_versions.c.customer_id == customer_id)
                    .values(version=m.state_versions.c.version + 1)
                )
            result = ExecutionResult(
                plan_id=command.plan_id,
                status="EXECUTED",
                steps=simulation.results,
                versions={c: v + 1 for c, v in versions.items()},
                committed_at=state.sim_now,
                error_code=None,
                replayed=False,
            )
            await conn.execute(
                m.plan_executions.insert().values(
                    customer_id=command.customer_id,
                    plan_id=command.plan_id,
                    plan_hash=command.plan_hash,
                    steps_digest=row["steps_digest"],
                    request_digest=row["request_digest"],
                    authorization_evidence=evidence,
                    result_json=result.model_dump(mode="json"),
                    committed_at=state.sim_now,
                )
            )
            await conn.execute(
                update(m.execution_intents)
                .where(
                    m.execution_intents.c.customer_id == command.customer_id,
                    m.execution_intents.c.plan_id == command.plan_id,
                )
                .values(status="COMMITTED")
            )
            if self.before_commit is not None:
                await self.before_commit(conn)
            return result

    async def query(self, customer_id, plan_id):
        async with self.db.connect() as conn:
            await self.customer(conn, customer_id)
            row = (
                await conn.execute(
                    select(m.execution_intents.c.status).where(
                        m.execution_intents.c.customer_id == customer_id,
                        m.execution_intents.c.plan_id == plan_id,
                    )
                )
            ).first()
            if row is None:
                raise BankError(Code.NOT_FOUND, 404)
            result = await conn.scalar(
                select(m.plan_executions.c.result_json).where(
                    m.plan_executions.c.customer_id == customer_id,
                    m.plan_executions.c.plan_id == plan_id,
                )
            )
            return {"status": row[0], "result": result}

    async def reconcile(self, plan_id, command):
        try:
            async with self.db.begin() as conn:
                await self.customer(conn, command.customer_id)
                now = self.wall_clock()
                # INSERT conflicts serialize with a concurrent prepare before either can commit.
                await conn.execute(text("SET LOCAL lock_timeout='3s'"))
                await conn.execute(
                    insert(m.execution_intents)
                    .values(
                        customer_id=command.customer_id,
                        plan_id=plan_id,
                        attempt=1,
                        plan_hash=command.plan_hash,
                        steps_digest="",
                        request_digest="",
                        authorization_evidence={},
                        evidence_history=[],
                        status="CANCELLED",
                        deadline_wall=now,
                        deadline_cap_wall=now,
                        created_at_wall=now,
                    )
                    .on_conflict_do_nothing()
                )
                row = await self.intent(conn, command.customer_id, plan_id)
                if row["plan_hash"] != command.plan_hash:
                    raise BankError(Code.IDEMPOTENCY_CONFLICT)
                if row["status"] == "COMMITTED":
                    result = await conn.scalar(
                        select(m.plan_executions.c.result_json).where(
                            m.plan_executions.c.customer_id == command.customer_id,
                            m.plan_executions.c.plan_id == plan_id,
                        )
                    )
                    return {"status": "COMMITTED", "result": result}
                await conn.execute(
                    update(m.execution_intents)
                    .where(
                        m.execution_intents.c.customer_id == command.customer_id,
                        m.execution_intents.c.plan_id == plan_id,
                    )
                    .values(status="CANCELLED")
                )
                return {"status": "CANCELLED", "result": None}
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise BankError(Code.EXECUTION_IN_PROGRESS) from exc
            raise
