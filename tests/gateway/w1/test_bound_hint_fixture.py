"""Pure checks that revocation tests use the real bound publisher, not bypass seeds."""

import asyncio
from copy import deepcopy

import pytest

from services.gateway.app.errors import GatewayError
from services.gateway.app.hint_sources import validate_hint_source
from services.gateway.app.utterance.parser import parse
from tests.gateway.test_hint_publication_unit import NOW, MemoryBoundary
from tests.gateway.w1.db.bound_hint_fixture import bound_transfer_hint
from tests.gateway.w1.fixtures import bank_state


def boundary():
    db = MemoryBoundary()
    db.tables["sessions"][0]["customer_id"] = "cus_zs"
    root = db.tables["utterances"][0]
    root["text"] = "给王五800"
    root["normalized_scope"] = parse("给王五800", bank_state(), "cus_zs").scope.model_dump(
        mode="json"
    )
    return db


def test_existing_scoped_root_gets_a_strictly_smaller_valid_bound_hint_without_mutation():
    async def run():
        db = boundary()
        before = deepcopy(db.tables["utterances"])
        hint = await bound_transfer_hint(db, "ses_current", NOW, root_id="utt_one")
        assert db.tables["utterances"] == before
        row = db.tables["hints"][0]
        assert row["scope_json"]["transfers"][0]["amount_cents"] == 30000
        assert row["parent_plan_id"] == hint.plan_id and hint.root_id == "utt_one"
        await validate_hint_source(db, db.tables["sessions"][0], row, NOW)

    asyncio.run(run())


def test_independent_fixture_root_does_not_reactivate_any_old_root():
    async def run():
        db = boundary()
        # The SQL boundary stand-in returns its sole root, so this case starts
        # with none; DB tests assert old + fresh IDs separately in real SQL.
        db.tables["utterances"] = []
        hint = await bound_transfer_hint(db, "ses_current", NOW)
        assert len(db.tables["utterances"]) == 1
        assert db.tables["utterances"][0]["id"] == hint.root_id
        assert db.tables["utterances"][0]["status"] == "ACTIVE"

    asyncio.run(run())


@pytest.mark.parametrize("status", ["SUPERSEDED", "CANCELLED"])
def test_old_inactive_root_cannot_be_reused_by_the_bound_fixture(status):
    async def run():
        db = boundary()
        db.tables["utterances"][0]["status"] = status
        with pytest.raises(GatewayError):
            await bound_transfer_hint(db, "ses_current", NOW, root_id="utt_one")
        assert db.tables["utterances"][0]["status"] == status
        assert db.tables["hints"] == []

    asyncio.run(run())


def test_clarifying_parent_cannot_issue_the_fixture_hint():
    async def run():
        db = boundary()
        db.tables["utterances"][0]["parse_status"] = "CLARIFYING"
        with pytest.raises(GatewayError):
            await bound_transfer_hint(db, "ses_current", NOW, root_id="utt_one")
        assert db.tables["utterances"][0]["parse_status"] == "CLARIFYING"
        assert db.tables["hints"] == []

    asyncio.run(run())
