"""Memo/alias warnings add risk without replacing mandatory source checks."""

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.schemas.enums import FindingEffect
from services.gateway.app.taint.authorization import AuthorizationEvidence
from tests.contracts.samples import step
from tests.gateway.w2.test_provenance import run, source
from tests.gateway.w2.test_sources import ledger, verify


def text_step(tool, value):
    param = "alias" if tool == "add_payee" else "memo"
    s = step(tool, **{param: value})
    return s, param


@pytest.mark.parametrize("tool", ["transfer", "create_split_request", "add_payee"])
@pytest.mark.parametrize(
    "value",
    [
        "收据12345678",
        "收据１２３４５６７８",
        "收据1234 5678",
        "报销100元",
        "报销100.50元",
        "报销100人民币",
        "报销人民币100",
        "报销100 CNY",
        "报销CNY 100.50",
        "报销cny100",
        "报销RMB 100",
        "报销100rmb",
        "报销¥100",
        "报销￥100.50",
        "报销１２０元",
    ],
)
def test_user_memo_or_alias_with_sensitive_content_adds_precise_yellow(tool, value):
    s, param = text_step(tool, value)
    s["arg_sources"] = [source(param, ref="utt_1", span=[0, len(value)])]
    ctx = run([s], text=value)
    assert ctx.provenance.arg_sources["s1"][param]["trust"] == "user"
    assert [f.effect for f in ctx.findings if f.code is Code.MEMO_SENSITIVE_CONTENT] == [
        FindingEffect.RISK_YELLOW
    ]
    assert all(f.step_id == "s1" for f in ctx.findings if f.code is Code.MEMO_SENSITIVE_CONTENT)


@pytest.mark.parametrize("tool", ["transfer", "create_split_request", "add_payee"])
@pytest.mark.parametrize("value", ["正常备注", "收据1234567", "分摊100", "", None])
def test_ordinary_text_and_seven_digits_do_not_add_sensitive_warning(tool, value):
    s, param = text_step(tool, value)
    if value:
        s["arg_sources"] = [source(param, ref="utt_1", span=[0, len(value)])]
    ctx = run([s], text=value or "普通说明")
    assert not any(f.code is Code.MEMO_SENSITIVE_CONTENT for f in ctx.findings)


@pytest.mark.parametrize("tool", ["transfer", "create_split_request", "add_payee"])
@pytest.mark.parametrize("untrusted", [True, False])
def test_sensitive_read_text_keeps_source_deny_alongside_yellow(monkeypatch, tool, untrusted):
    s, param = text_step(tool, "报销100元")
    s["arg_sources"] = [source(param, "read", ref="src_1", pointer="/text")]
    ctx = verify(
        monkeypatch,
        s,
        ledger({"text": "报销100元"}, untrusted=("/text",) if untrusted else ()),
    )
    assert ctx.provenance.arg_sources["s1"][param]["trust"] == "untrusted"
    assert ctx.has_hard_deny()
    assert any(
        f.code is Code.TAINTED_SENSITIVE_ARG and f.effect is FindingEffect.DENY
        for f in ctx.findings
    )
    assert any(
        f.code is Code.MEMO_SENSITIVE_CONTENT and f.effect is FindingEffect.RISK_YELLOW
        for f in ctx.findings
    )


@pytest.mark.parametrize("tool", ["transfer", "create_split_request", "add_payee"])
@pytest.mark.parametrize("declaration", [False, True])
def test_sensitive_text_without_valid_user_source_keeps_replan(monkeypatch, tool, declaration):
    s, param = text_step(tool, "6222020000000099")
    if declaration:
        s["arg_sources"] = [source(param, ref="utt_missing", span=[0, 16])]
    ctx = verify(monkeypatch, s, {}, text="普通说明")
    assert ctx.provenance.arg_sources["s1"][param]["trust"] == "unknown"
    assert any(
        f.code is Code.UNKNOWN_SENSITIVE_ARG
        and f.effect is FindingEffect.REPLAN
        and f.resolver == "user"
        for f in ctx.findings
    )
    assert any(
        f.code is Code.MEMO_SENSITIVE_CONTENT and f.effect is FindingEffect.RISK_YELLOW
        for f in ctx.findings
    )


@pytest.mark.parametrize("tool", ["transfer", "create_split_request", "add_payee"])
def test_saved_action_source_remains_user_and_adds_yellow(monkeypatch, tool):
    s, param = text_step(tool, "报销100元")
    s["arg_sources"] = [source(param, "action", ref="act_1")]

    async def action_evidence(_ctx):
        return AuthorizationEvidence(
            sources={"act_1": {"type": "action", "normalized_fields": {param: "报销100元"}}}
        )

    monkeypatch.setattr(
        "services.gateway.app.pipeline.provenance.authorization_evidence", action_evidence
    )
    ctx = run([s])
    assert ctx.provenance.arg_sources["s1"][param]["trust"] == "user"
    assert any(
        f.code is Code.MEMO_SENSITIVE_CONTENT and f.effect is FindingEffect.RISK_YELLOW
        for f in ctx.findings
    )
