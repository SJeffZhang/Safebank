"""Persistent source-chain fixtures; these do not pretend to execute W1 G3."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from safebank_contracts.plan import PlanDraft
from sqlalchemy import insert, update
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.enums import ServiceKind, UtteranceStatus
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.schemas.scope import (
    AuthorizationInputs,
    NormalizedScope,
    ScopeTransfer,
    UtteranceRef,
)
from services.gateway.app.tables import form_actions, hints, sessions, utterances
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context, draft
from tests.gateway.w2.test_provenance import source


async def adopted_fixture(db, *, kind="hint", adopted=True):
    token = uuid4().hex
    sid, root, source_id = (
        "ses_" + token,
        "utt_" + token,
        ("hnt_" if kind == "hint" else "act_") + token,
    )
    now = datetime.now(UTC)
    args = step(amount_cents=10000)["args"]
    scope = NormalizedScope(
        action_kinds=("TRANSFER_EXTERNAL",),
        from_account_id="acc_a",
        transfers=(
            ScopeTransfer(
                from_account_id="acc_a", target_fingerprint="pye_bfp", amount_cents=10000
            ),
        ),
        fields=args if kind == "action" else {},
    )
    ref = UtteranceRef(
        id=root,
        kind=kind,
        text=None,
        scope=scope,
        parse_status="SCOPED",
        created_at_wall=now.isoformat(),
        expires_at_wall=(now + timedelta(minutes=10)).isoformat(),
    )
    ctx = context()
    ctx.session_id, ctx.origin = sid, "agent"
    ctx.identity = Caller(
        ServiceKind.A, customer_id="cus_a", sid=sid, utts=(root,), scopes=frozenset({"assistant"})
    )
    ctx.authorization = AuthorizationInputs(
        sid=sid,
        customer_id="cus_a",
        utterance_ids=(root,),
        utterances={root: ref},
        scope_ids=(root,),
        statuses={root: UtteranceStatus.ACTIVE},
    )
    body = draft([step(amount_cents=10000)])
    body.update(session_id=sid, utterance_ids=[root])
    body["steps"][0]["arg_sources"] = [source("amount_cents", kind, ref=source_id)]
    ctx.plan_draft = PlanDraft.model_validate(body)
    async with db.begin() as conn:
        await conn.execute(
            insert(sessions).values(id=sid, customer_id="cus_a", created_at_wall=now)
        )
        table = hints if kind == "hint" else form_actions
        values = {
            "id": source_id,
            "sid": sid,
            "created_at_wall": now,
            "expires_at_wall": now + timedelta(minutes=10),
            "used_by": root if adopted else None,
        }
        if kind == "hint":
            values.update(scope_json=scope.model_dump(mode="json"), label="转账100元")
        else:
            values.update(kind="FORM", action_kind="TRANSFER_EXTERNAL", normalized_fields=args)
        await conn.execute(insert(table).values(**values))
        await conn.execute(
            insert(utterances).values(
                id=root,
                sid=sid,
                kind=kind,
                text=None,
                normalized_scope=scope.model_dump(mode="json"),
                source_id=source_id,
                parent_ids=[],
                status="ACTIVE",
                parse_status="SCOPED",
                created_at_wall=now,
                expires_at_wall=now + timedelta(minutes=10),
                authorization_epoch=0,
            )
        )
    return ctx, table, source_id


async def trust_for(db, ctx, param="amount_cents"):
    ctx.findings.clear()
    async with db.connect() as conn:
        ctx.conn = conn
        await ProvenanceStage().run(ctx)
    return ctx.provenance.arg_sources["s1"][param]["trust"]


@pytest.mark.parametrize("kind", ["hint", "action"])
def test_active_adopted_source_uses_utterance_claim_identity(gateway_db, kind):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, source_id = await adopted_fixture(db, kind=kind)
            assert ctx.authorization.scope_ids == ctx.authorization.utterance_ids
            assert source_id not in ctx.authorization.scope_ids
            assert await trust_for(db, ctx) == "user"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("used_by", [None, "utt_unrelated", "pln_1"])
def test_agent_cannot_use_unadopted_or_differently_adopted_source(gateway_db, used_by):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, table, id_ = await adopted_fixture(db)
            async with db.begin() as conn:
                await conn.execute(update(table).where(table.c.id == id_).values(used_by=used_by))
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_agent_cannot_masquerade_as_direct_form_adapter(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db, kind="action", adopted=False)
            ctx.origin = "app_form"
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


async def with_typed_parent(db, ctx, *, parent_kind="typed"):
    root = ctx.authorization.utterance_ids[0]
    ref = ctx.authorization.utterances[root]
    parent = "utt_parent_" + uuid4().hex
    now = datetime.fromisoformat(ref.created_at_wall)
    parent_scope = NormalizedScope(
        action_kinds=("TRANSFER_EXTERNAL",),
        transfers=(
            ScopeTransfer(
                from_account_id="acc_a", target_fingerprint="pye_bfp", amount_cents=99999
            ),
        ),
    )
    async with db.begin() as conn:
        await conn.execute(
            insert(utterances).values(
                id=parent,
                sid=ctx.session_id,
                kind=parent_kind,
                text="转账100元",
                source_id=None,
                normalized_scope=parent_scope.model_dump(mode="json"),
                parent_ids=[],
                status="ACTIVE",
                parse_status="INTENT_UNCLEAR",
                created_at_wall=now,
                expires_at_wall=now + timedelta(minutes=10),
                authorization_epoch=0,
            )
        )
        await conn.execute(
            update(utterances).where(utterances.c.id == root).values(parent_ids=[parent])
        )
    ctx.authorization = ctx.authorization.model_copy(
        update={"utterances": {root: ref.model_copy(update={"parent_ids": (parent,)})}}
    )
    return parent


def set_sources(ctx, values):
    body = ctx.plan_draft.model_dump(mode="json")
    body["steps"][0]["arg_sources"] = values
    ctx.plan_draft = PlanDraft.model_validate(body)


def test_parent_text_is_reachable_without_becoming_a_claim_or_new_scope(gateway_db):
    async def run():
        from services.gateway.app.taint.authorization import authorization_evidence

        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            parent = await with_typed_parent(db, ctx)
            set_sources(ctx, [source("amount_cents", ref=parent, span=[2, 6])])
            assert await trust_for(db, ctx) == "user"
            async with db.connect() as conn:
                ctx.conn = conn
                proof = await authorization_evidence(ctx)
            assert parent in proof.utterances
            assert parent not in ctx.authorization.utterance_ids
            assert len(ctx.authorization.utterances) == 1
            assert (
                ctx.authorization.utterances[next(iter(ctx.authorization.utterances))]
                .scope.transfers[0]
                .amount_cents
                == 10000
            )
            # A second action matching only the parent's old scope remains forbidden.
            from services.gateway.app.pipeline.context import ResolvedStep
            from services.gateway.app.pipeline.intent import IntentStage

            body = ctx.plan_draft.model_dump(mode="json")
            extra = step(amount_cents=99999)
            extra["step_id"] = "s2"
            body["steps"].append(extra)
            ctx.plan_draft = PlanDraft.model_validate(body)
            ctx.steps = tuple(
                ResolvedStep(
                    item.step_id,
                    item.tool,
                    item.args.model_dump(mode="json"),
                    "TRANSFER_EXTERNAL",
                    "YELLOW",
                    True,
                )
                for item in ctx.plan_draft.steps
            )
            ctx.findings.clear()
            async with db.connect() as conn:
                ctx.conn = conn
                await IntentStage().run(ctx)
            assert not ctx.intent.matched
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"status": "SUPERSEDED"},
        {"status": "CONSUMED"},
        {"status": "CANCELLED"},
        {"authorization_epoch": 1},
        {"sid": "ses_unrelated"},
        {"expires_at_wall": datetime.now(UTC) - timedelta(seconds=1)},
        {"created_at_wall": lambda: datetime.now(UTC) + timedelta(minutes=1)},
    ],
)
def test_invalid_parent_invalidates_the_whole_source_proof(gateway_db, change):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            parent = await with_typed_parent(db, ctx)
            async with db.begin() as conn:
                if change.get("sid"):
                    await conn.execute(
                        insert(sessions).values(
                            id=change["sid"], customer_id="cus_b", created_at_wall=datetime.now(UTC)
                        )
                    )
                await conn.execute(
                    update(utterances)
                    .where(utterances.c.id == parent)
                    .values(
                        **{
                            key: value() if callable(value) else value
                            for key, value in change.items()
                        }
                    )
                )
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "defect",
    [
        "cycle",
        "depth",
        "nodes",
        "unrelated",
        "source_claim",
        "jwt",
        "epoch",
        "revoked",
        "owner",
        "root_ref",
    ],
)
def test_malformed_or_unbound_lineage_is_rejected(gateway_db, defect):
    async def run():
        from services.gateway.app.taint.authorization import MAX_LINEAGE_DEPTH, MAX_LINEAGE_NODES

        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, id_ = await adopted_fixture(db)
            root = ctx.authorization.utterance_ids[0]
            if defect in ("cycle", "depth", "nodes", "unrelated"):
                parent = await with_typed_parent(db, ctx)
                async with db.begin() as conn:
                    if defect == "cycle":
                        await conn.execute(
                            update(utterances)
                            .where(utterances.c.id == parent)
                            .values(parent_ids=[root])
                        )
                    elif defect in ("depth", "nodes"):
                        from sqlalchemy import select

                        row = dict(
                            (
                                await conn.execute(
                                    select(utterances).where(utterances.c.id == parent)
                                )
                            )
                            .mappings()
                            .one()
                        )
                        extra = [
                            "utt_extra_" + uuid4().hex
                            for _ in range(
                                MAX_LINEAGE_DEPTH if defect == "depth" else MAX_LINEAGE_NODES
                            )
                        ]
                        for i, node in enumerate(extra):
                            await conn.execute(
                                insert(utterances).values(
                                    **(
                                        row
                                        | {
                                            "id": node,
                                            "parent_ids": extra[i + 1 : i + 2]
                                            if defect == "depth"
                                            else [],
                                        }
                                    )
                                )
                            )
                        await conn.execute(
                            update(utterances)
                            .where(utterances.c.id == parent)
                            .values(parent_ids=extra[:1] if defect == "depth" else extra)
                        )
                    else:
                        # It exists and is ACTIVE, but is not in the root's chain.
                        await conn.execute(
                            update(utterances).where(utterances.c.id == root).values(parent_ids=[])
                        )
                        ref = ctx.authorization.utterances[root].model_copy(
                            update={"parent_ids": ()}
                        )
                        ctx.authorization = ctx.authorization.model_copy(
                            update={"utterances": {root: ref}}
                        )
                        set_sources(ctx, [source("amount_cents", ref=parent, span=[2, 6])])
            elif defect == "source_claim":
                ctx.authorization = ctx.authorization.model_copy(update={"scope_ids": (id_,)})
            elif defect == "jwt":
                ctx.identity = Caller(
                    ServiceKind.A,
                    customer_id="cus_a",
                    sid=ctx.session_id,
                    utts=("utt_other",),
                    scopes=frozenset({"assistant"}),
                )
            elif defect == "root_ref":
                ref = ctx.authorization.utterances[root].model_copy(update={"text": "forged"})
                ctx.authorization = ctx.authorization.model_copy(update={"utterances": {root: ref}})
            else:
                values = (
                    {"authorization_epoch": 1}
                    if defect == "epoch"
                    else {"revoked_at_wall": datetime.now(UTC)}
                    if defect == "revoked"
                    else {"customer_id": "cus_b"}
                )
                async with db.begin() as conn:
                    await conn.execute(
                        update(sessions).where(sessions.c.id == ctx.session_id).values(**values)
                    )
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "fields, expected",
    [
        ([{"field": "payee_id", "entity_id": "pye_b"}], "user"),
        ([{"field": "payee_id", "entity_id": "pye_wrong"}], "unknown"),
        ([{"field": "payee_id", "entity_id": "pye_b"}] * 2, "unknown"),
        (
            [
                {"field": "payee_id", "entity_id": "pye_b"},
                {"field": "payee_id", "entity_id": "pye_wrong"},
            ],
            "unknown",
        ),
        ({"payee_id": "pye_b", "amount_cents": 10000}, "unknown"),
    ],
)
def test_clarification_restores_only_stored_selected_slots(gateway_db, fields, expected):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, table, id_ = await adopted_fixture(db, kind="action")
            parent = await with_typed_parent(db, ctx)
            async with db.begin() as conn:
                await conn.execute(
                    update(table)
                    .where(table.c.id == id_)
                    .values(
                        kind="CLARIFICATION", parent_utterance_id=parent, normalized_fields=fields
                    )
                )
            set_sources(
                ctx,
                [
                    source("payee_id", "action", ref=id_),
                    source("amount_cents", ref=parent, span=[2, 6]),
                ],
            )
            assert await trust_for(db, ctx, "payee_id") == expected
            if expected == "user":
                assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "user"
                set_sources(ctx, [source("amount_cents", "action", ref=id_)])
                assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_adopted_source_on_parent_is_reachable_from_active_typed_root(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            old = ctx.authorization.utterance_ids[0]
            previous = ctx.authorization.utterances[old]
            root = "utt_child_" + uuid4().hex
            ref = previous.model_copy(
                update={"id": root, "kind": "typed", "text": "确认", "parent_ids": (old,)}
            )
            async with db.begin() as conn:
                await conn.execute(
                    insert(utterances).values(
                        id=root,
                        sid=ctx.session_id,
                        kind="typed",
                        text="确认",
                        source_id=None,
                        parent_ids=[old],
                        normalized_scope=ref.scope.model_dump(mode="json"),
                        parse_status="SCOPED",
                        status="ACTIVE",
                        authorization_epoch=0,
                        created_at_wall=datetime.fromisoformat(ref.created_at_wall),
                        expires_at_wall=datetime.fromisoformat(ref.expires_at_wall),
                    )
                )
            ctx.authorization = ctx.authorization.model_copy(
                update={
                    "utterance_ids": (root,),
                    "scope_ids": (root,),
                    "utterances": {root: ref},
                    "statuses": {root: UtteranceStatus.ACTIVE},
                }
            )
            ctx.identity = Caller(
                ServiceKind.A,
                customer_id="cus_a",
                sid=ctx.session_id,
                utts=(root,),
                scopes=frozenset({"assistant"}),
            )
            body = ctx.plan_draft.model_dump(mode="json")
            body["utterance_ids"] = [root]
            ctx.plan_draft = PlanDraft.model_validate(body)
            assert await trust_for(db, ctx) == "user"
        finally:
            await db.dispose()

    asyncio.run(run())


async def direct_fixture(db):
    from sqlalchemy import delete

    ctx, table, id_ = await adopted_fixture(db, kind="action", adopted=False)
    root = ctx.authorization.utterance_ids[0]
    old = ctx.authorization.utterances[root]
    ref = old.model_copy(update={"id": id_})
    async with db.begin() as conn:
        await conn.execute(delete(utterances).where(utterances.c.id == root))
    ctx.origin = "app_form"
    ctx.identity = Caller(ServiceKind.F, customer_id="cus_a", scopes=frozenset({"app"}))
    ctx.authorization = ctx.authorization.model_copy(
        update={
            "utterance_ids": (id_,),
            "scope_ids": (id_,),
            "utterances": {id_: ref},
            "statuses": {id_: UtteranceStatus.ACTIVE},
        }
    )
    body = ctx.plan_draft.model_dump(mode="json")
    body["utterance_ids"] = [id_]
    ctx.plan_draft = PlanDraft.model_validate(body)
    return ctx, table, id_


@pytest.mark.parametrize("used_by", [None, "pln_1"])
def test_direct_form_is_credential_bound_without_fake_utterance_row(gateway_db, used_by):
    async def run():
        from sqlalchemy import select

        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, table, id_ = await direct_fixture(db)
            async with db.begin() as conn:
                await conn.execute(update(table).where(table.c.id == id_).values(used_by=used_by))
                assert (
                    await conn.execute(
                        select(utterances.c.id).where(utterances.c.sid == ctx.session_id)
                    )
                ).all() == []
            assert await trust_for(db, ctx) == "user"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "defect",
    [
        "used_plan",
        "used_utterance",
        "expired",
        "clarification",
        "agent",
        "timestamp",
        "fields",
        "kind",
        "status",
        "scope_claim",
    ],
)
def test_direct_form_rejects_wrong_source_or_adapter(gateway_db, defect):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, table, id_ = await direct_fixture(db)
            ref = ctx.authorization.utterances[id_]
            values = {}
            if defect == "used_plan":
                values["used_by"] = "pln_other"
            elif defect == "used_utterance":
                values["used_by"] = "utt_adopted"
            elif defect == "expired":
                values["expires_at_wall"] = datetime.now(UTC) - timedelta(seconds=1)
            elif defect == "clarification":
                values["kind"] = "CLARIFICATION"
            elif defect == "agent":
                ctx.identity = Caller(
                    ServiceKind.A,
                    customer_id="cus_a",
                    sid=ctx.session_id,
                    utts=(id_,),
                    scopes=frozenset({"assistant"}),
                )
                ctx.origin = "agent"
            elif defect == "status":
                ctx.authorization = ctx.authorization.model_copy(
                    update={"statuses": {id_: UtteranceStatus.CONSUMED}}
                )
            elif defect == "scope_claim":
                ctx.authorization = ctx.authorization.model_copy(
                    update={"scope_ids": ("act_other",)}
                )
            else:
                ref = (
                    ref.model_copy(update={"expires_at_wall": "bad-time"})
                    if defect == "timestamp"
                    else ref.model_copy(update={"kind": "typed"})
                    if defect == "kind"
                    else ref.model_copy(
                        update={
                            "scope": ref.scope.model_copy(
                                update={"fields": {"amount_cents": 10000}}
                            )
                        }
                    )
                )
                ctx.authorization = ctx.authorization.model_copy(update={"utterances": {id_: ref}})
            if values:
                async with db.begin() as conn:
                    await conn.execute(update(table).where(table.c.id == id_).values(**values))
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_parent_cannot_be_created_after_child_even_when_both_are_live(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            parent = await with_typed_parent(db, ctx)
            ref = next(iter(ctx.authorization.utterances.values()))
            async with db.begin() as conn:
                await conn.execute(
                    update(utterances)
                    .where(utterances.c.id == parent)
                    .values(
                        created_at_wall=datetime.fromisoformat(ref.created_at_wall)
                        + timedelta(microseconds=1)
                    )
                )
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


def test_missing_caller_never_falls_back_to_loaded_typed_span(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            parent = await with_typed_parent(db, ctx)
            ref = ctx.authorization.utterances[ctx.authorization.utterance_ids[0]]
            # A loaded-looking authorization alone is not caller evidence.
            ctx.authorization = ctx.authorization.model_copy(
                update={
                    "utterance_ids": (parent,),
                    "scope_ids": (parent,),
                    "utterances": {
                        parent: ref.model_copy(
                            update={
                                "id": parent,
                                "kind": "typed",
                                "text": "转账100元",
                                "parent_ids": (),
                            }
                        )
                    },
                    "statuses": {parent: UtteranceStatus.ACTIVE},
                }
            )
            ctx.identity = None
            set_sources(ctx, [source("amount_cents", ref=parent, span=[2, 6])])
            body = ctx.plan_draft.model_dump(mode="json")
            body["utterance_ids"] = [parent]
            ctx.plan_draft = PlanDraft.model_validate(body)
            assert await trust_for(db, ctx) == "unknown"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("defect", ["parent", "caller"])
def test_invalid_lineage_blocks_intent_even_without_a_user_source(gateway_db, defect):
    async def run():
        from services.gateway.app.pipeline.context import ResolvedStep
        from services.gateway.app.pipeline.intent import IntentStage

        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            ctx, _, _ = await adopted_fixture(db)
            parent = await with_typed_parent(db, ctx)
            if defect == "parent":
                async with db.begin() as conn:
                    await conn.execute(
                        update(utterances)
                        .where(utterances.c.id == parent)
                        .values(status="SUPERSEDED")
                    )
            else:
                ctx.identity = None
            item = ctx.plan_draft.steps[0]
            ctx.steps = (
                ResolvedStep(
                    item.step_id,
                    item.tool,
                    item.args.model_dump(mode="json"),
                    "TRANSFER_EXTERNAL",
                    "YELLOW",
                    True,
                ),
            )
            async with db.connect() as conn:
                ctx.conn = conn
                await IntentStage().run(ctx)
            assert not ctx.intent.matched
            assert any(
                f.code.value == "INTENT_SCOPE_EXCEEDED" and f.resolver == "user"
                for f in ctx.findings
            )
        finally:
            await db.dispose()

    asyncio.run(run())
