"""Stage ①: revalidate the frozen wire contracts at the pipeline boundary."""

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft

from ..errors import GatewayError
from ..schemas.requests import AppFormPlanRequest
from .context import EvalContext, Stage


class ContractStage(Stage):
    name = "contract"

    async def run(self, ctx: EvalContext) -> None:
        try:
            if ctx.plan_draft is not None:
                raw = (
                    ctx.plan_draft.model_dump(mode="json")
                    if isinstance(ctx.plan_draft, PlanDraft)
                    else ctx.plan_draft
                )
                ctx.plan_draft = PlanDraft.model_validate(raw)
            elif isinstance(ctx.request, AppFormPlanRequest):
                AppFormPlanRequest.model_validate(ctx.request.model_dump(mode="json"))
            elif ctx.customer_id is not None:
                raise ValueError("missing plan")
            # Empty context is retained for W0's infrastructure-only pipeline probe.
        except (ValidationError, ValueError, TypeError) as exc:
            raise GatewayError(Code.CONTRACT_INVALID, 422) from exc


stage = ContractStage()
