"""Strictness of the C1b local state models (StrictBool / Slot / ReadRecord)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.agent.app.graph.state import ReadRecord, Slot, Understanding


def test_needs_write_rejects_loose_bool_coercions():
    for bad in ("yes", "off", "no", 0, 1, "1"):
        with pytest.raises(ValidationError):
            Understanding(intents=["transfer"], slots=(), needs_write=bad)
    for good in (True, False):
        assert Understanding(intents=["transfer"], slots=(), needs_write=good).needs_write is good


def test_unclear_rejects_loose_bool_coercions():
    with pytest.raises(ValidationError):
        Understanding(intents=["transfer"], slots=(), needs_write=True, unclear="yes")
    assert Understanding(intents=[], slots=(), needs_write=False).unclear is False


def test_slot_rejects_negative_intent_index_and_bad_span():
    with pytest.raises(ValidationError):
        Slot(intent_index=-1, name="payee", value="王五", span=[0, 1])
    with pytest.raises(ValidationError):
        Slot(intent_index=0, name="payee", value="王五", span=[5, 3])
    with pytest.raises(ValidationError):
        Slot(intent_index=0, name="payee", value="王五", span=[0, 0])
    assert Slot(intent_index=0, name="payee", value="王五", span=[3, 5]).span == (3, 5)


def test_read_record_extended_round_trips():
    record = ReadRecord(
        source_ref="src_a",
        tool="get_accounts",
        args={"history_days": 7},
        data={"accounts": [{"account_id": "acc_a"}]},
        untrusted_paths=("/memo",),
        versions={"accounts": 3},
        observed_at="2026-10-24T00:00:00+08:00",
    )
    assert record.tool == "get_accounts"
    assert record.data["accounts"][0]["account_id"] == "acc_a"
    assert record.versions == {"accounts": 3}
    # defaults keep the C1a construction working
    assert ReadRecord(source_ref="rd_a").data == {}
