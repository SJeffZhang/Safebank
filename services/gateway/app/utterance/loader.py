"""Load live, caller-bound authorization inputs; shared W1 → W2/W3 contract."""

from __future__ import annotations

from datetime import UTC, datetime

from safebank_contracts.codes import Code
from sqlalchemy import select

from .. import tables as t
from ..errors import GatewayError
from ..schemas.enums import ServiceKind, UtteranceStatus
from ..schemas.scope import AuthorizationInputs, UtteranceRef
from .parser import scope_is_complete
from .runtime import session_for


def as_ref(row) -> UtteranceRef:
    return UtteranceRef(
        id=row["id"],
        kind=row["kind"],
        text=row["text"],
        scope=row["normalized_scope"],
        parse_status=row["parse_status"],
        echoes_untrusted=row["echoes_untrusted"],
        parent_ids=tuple(row["parent_ids"]),
        clarification_id=row["clarification_id"],
        created_at_wall=row["created_at_wall"].isoformat(),
        expires_at_wall=row["expires_at_wall"].isoformat(),
    )


async def load_for_plan(conn, sid, ids, caller) -> AuthorizationInputs:
    """Reject expired, cancelled, superseded or differently-owned references."""
    now = datetime.now(UTC)
    session = await session_for(conn, sid, caller, now)
    if not ids or len(ids) != len(set(ids)):
        raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
    if caller.kind is ServiceKind.A and not set(ids).issubset(caller.utts):
        raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
    rows = (
        (
            await conn.execute(
                select(t.utterances).where(
                    t.utterances.c.sid == sid,
                    t.utterances.c.id.in_(ids),
                )
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(ids):
        raise GatewayError(Code.NOT_FOUND, 404)
    references = {}
    for row in rows:
        if (
            row["authorization_epoch"] != session["authorization_epoch"]
            or row["status"] == "CANCELLED"
        ):
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
        if row["status"] != "ACTIVE":
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
        if row["expires_at_wall"] is None or row["expires_at_wall"] <= now:
            raise GatewayError(Code.UTTERANCE_EXPIRED, 409)
        reference = as_ref(row)
        if row["parse_status"] != "SCOPED" or not scope_is_complete(reference.scope):
            raise GatewayError(Code.INTENT_UNCLEAR, 409)
        references[row["id"]] = reference
    return AuthorizationInputs(
        sid=sid,
        customer_id=caller.customer_id,
        utterance_ids=tuple(ids),
        utterances=references,
        scope_ids=tuple(ids),
        authorization_epoch=session["authorization_epoch"],
        statuses={id_: UtteranceStatus.ACTIVE for id_ in ids},
    )
