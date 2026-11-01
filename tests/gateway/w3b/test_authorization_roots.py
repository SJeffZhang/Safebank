"""In-memory query adapter exercises current-plan roots and persisted lineage.

Real PostgreSQL endpoint regressions live in db/test_current_authorization.py.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from safebank_contracts.plan import PlanDraft
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, Null

from services.gateway.app.api.plans import _load_authorization
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.schemas.enums import ServiceKind
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.schemas.scope import NormalizedScope
from services.gateway.app.taint.authorization import authorization_evidence
from tests.gateway.w3b.db.conftest import WALL, make_plan_draft_dict


def _value(expr, row):
    if isinstance(expr, BindParameter):
        return expr.value
    if isinstance(expr, Null):
        return None
    return row[expr.name]


def _matches(clause, row):
    left, right = _value(clause.left, row), _value(clause.right, row)
    if clause.operator is operators.in_op:
        return left in right
    if clause.operator is operators.is_:
        return left is right
    return clause.operator(left, right)


class MemoryConnection:
    """Evaluate ordinary SQL WHERE predicates over fixed persisted test rows."""

    def __init__(self, rows):
        self.rows = rows

    async def execute(self, query):
        name = query.get_final_froms()[0].name
        rows = [r for r in self.rows[name] if all(_matches(c, r) for c in query._where_criteria)]
        return SimpleNamespace(
            mappings=lambda: SimpleNamespace(
                all=lambda: rows, one_or_none=lambda: rows[0] if len(rows) == 1 else None
            )
        )


def _row(id_, parse_status="SCOPED", **updates):
    return {
        "id": id_,
        "sid": "ses_current",
        "kind": "typed",
        "text": "转账800元",
        "normalized_scope": NormalizedScope(action_kinds=("TRANSFER_EXTERNAL",)).model_dump(
            mode="json"
        ),
        "echoes_untrusted": False,
        "status": "ACTIVE",
        "parse_status": parse_status,
        "authorization_epoch": 3,
        "parent_ids": [],
        "source_id": None,
        "clarification_id": None,
        "created_at_wall": WALL - timedelta(minutes=1),
        "expires_at_wall": WALL + timedelta(minutes=5),
        **updates,
    }


def _connection(*rows):
    return MemoryConnection(
        {
            "utterances": list(rows),
            "sessions": [
                {
                    "id": "ses_current",
                    "customer_id": "cus_a",
                    "authorization_epoch": 3,
                    "revoked_at_wall": None,
                }
            ],
        }
    )


async def _evidence(conn, ids=("utt_current",), *, token_ids=None, customer="cus_a"):
    auth = await _load_authorization(conn, "ses_current", customer, WALL, source_ids=ids)
    ctx = EvalContext(
        authorization=auth,
        conn=conn,
        origin="agent",
        customer_id=customer,
        session_id="ses_current",
        wall_now=lambda: WALL,
        identity=Caller(
            ServiceKind.A,
            customer_id=customer,
            sid="ses_current",
            scopes=frozenset({"assistant"}),
            utts=token_ids or ids,
        ),
        plan_draft=PlanDraft.model_validate(
            make_plan_draft_dict(session_id="ses_current", utterance_ids=list(ids))
        ),
    )
    return auth, await authorization_evidence(ctx)


@pytest.mark.parametrize("old_status", ["NO_WRITE", "SCOPED"])
def test_loader_uses_only_declared_roots_despite_live_session_context(old_status):
    async def run():
        conn = _connection(_row("utt_old", old_status), _row("utt_current"))
        auth, evidence = await _evidence(conn)
        assert auth.utterance_ids == auth.scope_ids == ("utt_current",)
        assert set(auth.utterances) == {"utt_current"}
        assert set(evidence.utterances) == {"utt_current"}

    asyncio.run(run())


def test_loader_without_declared_roots_does_not_adopt_session_authority():
    async def run():
        auth = await _load_authorization(_connection(_row("utt_old")), "ses_current", "cus_a", WALL)
        assert not auth.utterance_ids and not auth.scope_ids and not auth.utterances

    asyncio.run(run())


def test_parent_chain_is_evidence_without_becoming_an_authorization_root():
    async def run():
        conn = _connection(
            _row("utt_parent", "INTENT_UNCLEAR"), _row("utt_current", parent_ids=["utt_parent"])
        )
        auth, evidence = await _evidence(conn)
        assert auth.utterance_ids == auth.scope_ids == ("utt_current",)
        assert set(evidence.utterances) == {"utt_current", "utt_parent"}

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "no_write",
        "jwt",
        "customer",
        "sid",
        "expired",
        "cancelled",
        "epoch",
        "revoked",
        "parent_epoch",
        "parent_sid",
    ],
)
def test_current_roots_preserve_credential_ownership_liveness_and_lineage_checks(mutation):
    async def run():
        root = _row("utt_current")
        parent = _row("utt_parent", "INTENT_UNCLEAR")
        conn = _connection(root, parent)
        customer, token_ids = "cus_a", None
        if mutation == "no_write":
            root["parse_status"] = "NO_WRITE"
        elif mutation == "jwt":
            token_ids = ("utt_other",)
        elif mutation == "customer":
            customer = "cus_other"
        elif mutation == "sid":
            root["sid"] = "ses_other"
        elif mutation == "expired":
            root["expires_at_wall"] = WALL
        elif mutation == "cancelled":
            root["status"] = "CANCELLED"
        elif mutation == "epoch":
            root["authorization_epoch"] = 2
        elif mutation == "revoked":
            conn.rows["sessions"][0]["revoked_at_wall"] = WALL
        else:
            root["parent_ids"] = ["utt_parent"]
            parent["authorization_epoch" if mutation == "parent_epoch" else "sid"] = (
                2 if mutation == "parent_epoch" else "ses_other"
            )
        _, evidence = await _evidence(conn, customer=customer, token_ids=token_ids)
        assert not evidence.utterances and not evidence.sources

    asyncio.run(run())
