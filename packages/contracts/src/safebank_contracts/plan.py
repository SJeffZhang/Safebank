from functools import reduce
from operator import or_
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictInt, create_model, model_validator

from .base import Contract, Timestamp
from .codes import Code
from .enums import Risk
from .tools import WRITE_ARGS, EntityRef, Id


class UtteranceSource(Contract):
    kind: Literal["utterance"]
    ref: Id
    span: tuple[StrictInt, StrictInt]

    @model_validator(mode="after")
    def ordered(self):
        if not 0 <= self.span[0] < self.span[1]:
            raise ValueError("invalid character span")
        return self


class ReadSource(Contract):
    kind: Literal["read"]
    ref: Id
    pointer: str


class StepSource(Contract):
    kind: Literal["step"]
    ref: Id


LeafSource = Annotated[UtteranceSource | ReadSource | StepSource, Field(discriminator="kind")]


class DerivedSource(Contract):
    kind: Literal["derived"]
    template: Literal["cancelled_monthly_fees", "available_less_7d_charges"]
    inputs: Annotated[tuple[LeafSource, ...], Field(min_length=1, max_length=20)]


class HintSource(Contract):
    kind: Literal["hint"]
    ref: Id


class ActionSource(Contract):
    kind: Literal["action"]
    ref: Id


Source = Annotated[
    UtteranceSource | ReadSource | StepSource | DerivedSource | HintSource | ActionSource,
    Field(discriminator="kind"),
]


class ArgSource(Contract):
    param: Id
    source: Source


class StepBase(Contract):
    step_id: Id
    depends_on: tuple[Id, ...]
    arg_sources: tuple[ArgSource, ...]

    @model_validator(mode="after")
    def sources(self):
        params = [a.param for a in self.arg_sources]
        if len(set(params)) != len(params) or any(
            p not in self.args.__class__.model_fields for p in params
        ):
            raise ValueError("duplicate or unknown parameter source")
        return self


STEP_MODELS = {
    tool: create_model(
        tool + "Step", __base__=StepBase, tool=(Literal[tool], ...), args=(model, ...)
    )
    for tool, model in WRITE_ARGS.items()
}
Step = Annotated[reduce(or_, STEP_MODELS.values()), Field(discriminator="tool")]


class PlanBody(Contract):
    intents: tuple[Id, ...]
    steps: Annotated[tuple[Step, ...], Field(min_length=1, max_length=20)]
    rationale: str

    @model_validator(mode="after")
    def dag(self):
        seen = {}
        for step in self.steps:
            if step.step_id in seen or len(set(step.depends_on)) != len(step.depends_on):
                raise ValueError("duplicate step or dependency")
            if any(dep not in seen for dep in step.depends_on):
                raise ValueError("dependencies must refer to earlier steps")
            for value in step.args:
                arg = value[1]
                if isinstance(arg, EntityRef):
                    if arg.ref not in step.depends_on or seen.get(arg.ref) != "add_payee":
                        raise ValueError(
                            "entity reference must depend on an earlier matching creator"
                        )
            for source in step.arg_sources:
                leaves = (
                    source.source.inputs
                    if isinstance(source.source, DerivedSource)
                    else (source.source,)
                )
                if any(
                    isinstance(leaf, StepSource) and leaf.ref not in step.depends_on
                    for leaf in leaves
                ):
                    raise ValueError("step source is not a dependency")
            seen[step.step_id] = step.tool
        return self


class PlanDraft(PlanBody):
    request_id: Id
    session_id: Id
    utterance_ids: Annotated[tuple[Id, ...], Field(min_length=1)]


class InteractiveEvidence(Contract):
    type: Literal["INTERACTIVE"]
    origin: Literal["agent", "app_form"]
    scope_ids: Annotated[tuple[Id, ...], Field(min_length=1)]
    utterance_ids: tuple[Id, ...]
    evaluation_id: Id
    approved_evaluation_id: Id | None
    plan_hash: str
    approval_id: Id | None
    confirmed_view_hash: str | None
    final_view_hash: str
    risk: Risk
    confirmed_at_wall: Timestamp | None
    authorization_epoch: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def approval(self):
        fields = (self.approval_id, self.confirmed_view_hash, self.confirmed_at_wall)
        if self.risk != Risk.GREEN and any(v is None for v in fields):
            raise ValueError("non-green evidence requires confirmation fields")
        if self.approval_id is not None and self.confirmed_view_hash != self.final_view_hash:
            raise ValueError("confirmation differs from execution view")
        return self


class ScheduledEvidence(Contract):
    type: Literal["SCHEDULED_GRANT"]
    grant_id: Id
    scope_hash: str
    source_plan_id: Id
    approved_risk: Risk


class OrderEvidence(Contract):
    type: Literal["SYSTEM_ORDER"]
    order_id: Id


class MandateEvidence(Contract):
    type: Literal["MERCHANT_MANDATE"]
    instruction_id: Id
    billing_id: Id
    grant_id: Id


class DemoEvidence(Contract):
    type: Literal["DEMO_EVENT"]
    event_id: Id
    console_session_id: Id
    kind: Literal["EXTERNAL_CREDIT", "EXTERNAL_DEBIT", "SPLIT_PAYMENT"]


Evidence = Annotated[
    InteractiveEvidence | ScheduledEvidence | OrderEvidence | MandateEvidence | DemoEvidence,
    Field(discriminator="type"),
]


class Effect(Contract):
    type: str
    entity_id: str
    field: str
    before: JsonValue
    after: JsonValue


class Violation(Contract):
    code: Code
    step_id: str | None = None
    message: str
    hard: bool = True


class StepResult(Contract):
    step_id: Id
    tool: str
    status: Literal["EXECUTED", "FAILED", "ROLLED_BACK"]
    created_ids: dict[str, str]
    result: dict[str, JsonValue]
    effects: tuple[Effect, ...]
    code: Code | None = None


class ExecutionResult(Contract):
    plan_id: Id
    status: Literal["EXECUTED", "FAILED"]
    steps: tuple[StepResult, ...]
    versions: dict[str, StrictInt]
    committed_at: Timestamp | None
    error_code: Code | None
    replayed: bool


class Prepare(Contract):
    customer_id: Id
    plan_id: Id
    attempt: StrictInt = Field(ge=1)
    plan_hash: str
    steps: Annotated[tuple[Step, ...], Field(min_length=1, max_length=20)]
    deadline_wall: Timestamp
    deadline_cap_wall: Timestamp
    authorization_evidence: InteractiveEvidence

    @model_validator(mode="after")
    def match(self):
        PlanBody(intents=(), steps=self.steps, rationale="")
        if self.deadline_wall > self.deadline_cap_wall:
            raise ValueError("deadline exceeds immutable authorization cap")
        if self.authorization_evidence.plan_hash != self.plan_hash:
            raise ValueError("evidence hash mismatch")
        return self


class Execute(Contract):
    customer_id: Id
    plan_id: Id
    attempt: StrictInt = Field(ge=1)
    plan_hash: str
    steps: Annotated[tuple[Step, ...], Field(min_length=1, max_length=20)]
    expected_versions: dict[str, Annotated[StrictInt, Field(ge=0)]]
    clock_version: StrictInt = Field(ge=0)
    authorization_evidence: InteractiveEvidence


class Reconcile(Contract):
    customer_id: Id
    plan_hash: str
    cancel_if_uncommitted: Literal[True]


class InternalRead(Contract):
    customer_id: Id
    args: dict[str, JsonValue]


class SnapshotRequest(Contract):
    customer_id: Id
    steps: tuple[Step, ...]
    event_id: Id | None


class Ack(Contract):
    event_ids: tuple[Id, ...]
