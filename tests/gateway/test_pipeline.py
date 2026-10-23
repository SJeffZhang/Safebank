import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.pipeline import evaluate
from services.gateway.app.pipeline.context import Effect, EvalContext, Finding, Resolver, Stage


class Recorder(Stage):
    def __init__(self, name, log, finding=None):
        self.name = name
        self._log = log
        self._finding = finding

    async def run(self, ctx):
        self._log.append(self.name)
        if self._finding is not None:
            ctx.add_finding(self._finding)


def test_default_pipeline_runs_all_nine_stages_without_findings():
    async def run():
        ctx = EvalContext()
        await evaluate(ctx)
        assert ctx.findings == []
        assert ctx.short_circuit is False

    asyncio.run(run())


def test_deny_short_circuits_everything_except_audit():
    async def run():
        log = []

        stages = [
            Recorder("contract", log, finding=Finding(Code.RISKY_SEQUENCE, Effect.DENY)),
            Recorder("snapshot", log),
            Recorder("decision", log),
            Recorder("audit", log),
        ]
        ctx = EvalContext()
        await evaluate(ctx, stages=stages)
        assert log == ["contract", "audit"]
        assert ctx.short_circuit is True

    asyncio.run(run())


def test_replan_does_not_short_circuit():
    async def run():
        log = []

        stages = [
            Recorder(
                "intent",
                log,
                finding=Finding(Code.INSUFFICIENT_FUNDS, Effect.REPLAN, resolver=Resolver.USER),
            ),
            Recorder("snapshot", log),
            Recorder("audit", log),
        ]
        ctx = EvalContext()
        await evaluate(ctx, stages=stages)
        assert log == ["intent", "snapshot", "audit"]
        assert ctx.short_circuit is False

    asyncio.run(run())


def test_finding_replan_requires_resolver():
    with pytest.raises(ValueError):
        Finding(Code.INSUFFICIENT_FUNDS, Effect.REPLAN)
    Finding(Code.INSUFFICIENT_FUNDS, Effect.REPLAN, resolver=Resolver.USER)


def test_finding_non_replan_rejects_resolver():
    with pytest.raises(ValueError):
        Finding(Code.RISKY_SEQUENCE, Effect.DENY, resolver=Resolver.USER)
