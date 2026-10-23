"""Pure stage tests inject loaded evidence; db/ exercises the real proof loader."""

from datetime import datetime

import pytest

from services.gateway.app.schemas.enums import UtteranceStatus
from services.gateway.app.taint.authorization import AuthorizationEvidence


@pytest.fixture(autouse=True)
def isolated_stage_authorization(request, monkeypatch):
    if request.node.path.parent.name == "db":
        return

    async def fixture_evidence(ctx):
        auth = ctx.authorization
        nodes = (
            {}
            if auth is None
            else {
                id_: {"kind": ref.kind, "text": ref.text}
                for id_, ref in auth.utterances.items()
                if id_ in auth.utterance_ids
                and auth.statuses.get(id_) is UtteranceStatus.ACTIVE
                and datetime.fromisoformat(ref.expires_at_wall) > ctx.wall_now()
            }
        )
        return AuthorizationEvidence(utterances=nodes)

    monkeypatch.setattr(
        "services.gateway.app.pipeline.provenance.authorization_evidence", fixture_evidence
    )

    monkeypatch.setattr(
        "services.gateway.app.pipeline.intent.authorization_evidence", fixture_evidence
    )
