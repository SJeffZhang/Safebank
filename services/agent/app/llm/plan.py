"""``submit_action_plan`` adapter: propose a PlanDraft, never authorize or execute."""

from __future__ import annotations

import json
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError
from safebank_contracts.plan import PlanBody, PlanDraft
from safebank_contracts.schema import from_strict_plan, plan_tool

from services.agent.app.graph.state import AgentContext, AgentState, UtteranceKind
from services.agent.app.llm._common import LlmToolError, new_ulid, single_tool_call

_PLAN_TOOL = "submit_action_plan"

_SYSTEM = (
    "你是银行助手，把用户请求与已读信息整理成一份计划草稿；你不授权、不执行任何操作。"
    "金额一律用整数分；份额用四位小数字符串。实体引用用判别对象（kind=id/ref/none）；"
    "可空字符串用哨兵 __NULL__，可空整数用 -1，渠道三态用 KEEP/ON/OFF。"
    "arg_sources 每项 {param, source}；source.kind 只能是 utterance/read/step/hint/derived，"
    "derived 的 template 只能是 cancelled_monthly_fees 或 available_less_7d_charges。"
    "不要自填 request_id/session_id/utterance_ids；这些由代码补齐。"
)


class PlanAdapter:
    def __init__(
        self,
        llm,
        max_retries: int = 1,
        observer: Callable[[AIMessage], None] | None = None,
    ) -> None:
        self._model = llm.bind_tools(
            [plan_tool()],
            tool_choice={"type": "function", "function": {"name": _PLAN_TOOL}},
        )
        self._max_retries = max_retries
        self._observer = observer

    def __call__(self, state: AgentState, context: AgentContext) -> PlanDraft:
        note = ""
        for attempt in range(self._max_retries + 1):
            message = self._model.invoke(self._messages(state, note))
            if self._observer is not None:
                self._observer(message)
            try:
                args = single_tool_call(message, _PLAN_TOOL)
                body = PlanBody.model_validate(from_strict_plan(args))
                return self._to_draft(body, state)
            except (LlmToolError, ValidationError, ValueError) as exc:
                if attempt == self._max_retries:
                    raise
                note = f"上次计划未通过校验（{type(exc).__name__}），请重新生成合法计划。"
        raise AssertionError("unreachable")

    @staticmethod
    def _to_draft(body: PlanBody, state: AgentState) -> PlanDraft:
        utterances = state.get("utterances", [])
        return PlanDraft(
            request_id=f"req_{new_ulid()}",
            session_id=state["session_id"],
            utterance_ids=tuple(
                utterance.id
                for utterance in utterances
                if utterance.kind is not UtteranceKind.CONTEXT
            ),
            intents=body.intents,
            steps=body.steps,
            rationale=body.rationale,
        )

    @staticmethod
    def _messages(state: AgentState, note: str) -> list:
        parts = ["用户请求（id/kind/text；span 用从 0 开始的字符下标）："]
        for utterance in state.get("utterances", []):
            text = json.dumps(utterance.text, ensure_ascii=False)
            parts.append(f"- id={utterance.id} kind={utterance.kind.value} text={text}")
        reads = state.get("reads", [])
        if reads:
            parts.append("已读数据（source_ref 供 {kind:read, ref:<source_ref>, pointer} 引用）：")
            for read in reads:
                parts.append(
                    f"- tool={read.tool} source_ref={read.source_ref} "
                    f"data={json.dumps(read.data, ensure_ascii=False, default=str)}"
                )
        understanding = state.get("understanding")
        if understanding is not None:
            parts.append(
                "理解提案：intents="
                + ",".join(understanding.intents)
                + f" needs_write={understanding.needs_write}"
            )
        decision = state.get("decision")
        if decision is not None:
            findings = ";".join(
                f"{finding.code}({finding.resolver or '-'})" for finding in decision.findings
            )
            parts.append(f"上次决策：{decision.decision} findings={findings}")
        content = "\n".join(parts)
        if note:
            content = f"{content}\n\n{note}"
        return [SystemMessage(content=_SYSTEM), HumanMessage(content=content)]
