import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline import evaluate
from services.gateway.app.pipeline.context import EvalContext, Stage, StageStatus
from services.gateway.app.schemas.decision import FindingView
from services.gateway.app.schemas.enums import FindingEffect


class Recorder(Stage):
    def __init__(self, name, log, finding=None, always_run=False):
        self.name = name
        self.always_run = always_run
        self._log = log
        self._finding = finding

    async def run(self, ctx):
        self._log.append(self.name)
        if self._finding is not None:
            ctx.add_finding(self._finding)


class Boom(Stage):
    name = "boom"

    async def run(self, ctx):
        raise GatewayError(Code.CONTRACT_INVALID, 422)


def test_default_pipeline_runs_all_stages_without_findings():
    async def run():
        ctx = EvalContext()
        await evaluate(ctx)
        assert ctx.findings == []
        assert ctx.short_circuit is False

    asyncio.run(run())


def test_deny_skips_only_snapshot_preflight_workflow():
    async def run():
        log = []
        deny = FindingView(code=Code.RISKY_SEQUENCE, effect=FindingEffect.DENY)
        stages = [
            Recorder("contract", log),
            Recorder("entity", log, finding=deny),
            Recorder("provenance", log),
            Recorder("intent", log),
            Recorder("snapshot", log),
            Recorder("preflight", log),
            Recorder("workflow", log),
            Recorder("decision", log),
            Recorder("audit", log),
        ]
        ctx = EvalContext()
        await evaluate(ctx, stages=stages)
        assert log == ["contract", "entity", "provenance", "intent", "decision", "audit"]
        assert ctx.short_circuit is True

    asyncio.run(run())


def test_replan_does_not_short_circuit():
    async def run():
        log = []
        replan = FindingView(
            code=Code.INSUFFICIENT_FUNDS, effect=FindingEffect.REPLAN, resolver="user"
        )
        stages = [
            Recorder("intent", log, finding=replan),
            Recorder("snapshot", log),
            Recorder("audit", log),
        ]
        ctx = EvalContext()
        await evaluate(ctx, stages=stages)
        assert log == ["intent", "snapshot", "audit"]
        assert ctx.short_circuit is False

    asyncio.run(run())


def test_profile_disables_stages():
    async def run():
        log = []
        stages = [
            Recorder("contract", log),
            Recorder("entity", log),
            Recorder("provenance", log),
            Recorder("intent", log),
            Recorder("snapshot", log),
            Recorder("preflight", log),
            Recorder("workflow", log),
            Recorder("decision", log),
            Recorder("audit", log),
        ]
        ctx = EvalContext(profile="B0")
        await evaluate(ctx, stages=stages)
        assert log == ["contract", "entity", "snapshot", "decision", "audit"]
        assert ctx.stage_status["provenance"] is StageStatus.SKIPPED
        assert ctx.stage_status["workflow"] is StageStatus.SKIPPED

    asyncio.run(run())


def test_contract_error_propagates_as_gateway_error():
    async def run():
        ctx = EvalContext()
        with pytest.raises(GatewayError) as exc:
            await evaluate(ctx, stages=[Boom()])
        assert exc.value.code is Code.CONTRACT_INVALID

    asyncio.run(run())


def test_finding_replan_requires_resolver():
    with pytest.raises(ValueError):
        FindingView(code=Code.INSUFFICIENT_FUNDS, effect=FindingEffect.REPLAN)
    FindingView(code=Code.INSUFFICIENT_FUNDS, effect=FindingEffect.REPLAN, resolver="user")
