"""``report_understanding`` adapter: propose intents/slots/needs_write, never authorize."""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from services.agent.app.graph.state import AgentContext, AgentState, Understanding
from services.agent.app.llm._common import LlmToolError, single_tool_call
from services.agent.app.llm.schema import UNDERSTANDING_TOOL, understanding_tool

_SYSTEM = (
    "你是银行助手，只把用户请求解析为结构化理解，不执行、不授权任何操作。"
    "intents 是意图列表；slots 是槽位数组（intent_index/name/value/span）；"
    "needs_write 表示该请求是否需要写操作。不要在这里估算或决定金额。"
)


class UnderstandAdapter:
    def __init__(
        self,
        llm,
        max_retries: int = 1,
        observer: Callable[[AIMessage], None] | None = None,
    ) -> None:
        self._model = llm.bind_tools(
            [understanding_tool()],
            tool_choice={"type": "function", "function": {"name": UNDERSTANDING_TOOL}},
        )
        self._max_retries = max_retries
        self._observer = observer

    def __call__(self, state: AgentState, context: AgentContext) -> Understanding:
        note = ""
        for attempt in range(self._max_retries + 1):
            message = self._model.invoke(self._messages(state, note))
            if self._observer is not None:
                self._observer(message)
            try:
                args = single_tool_call(message, UNDERSTANDING_TOOL)
                return Understanding.model_validate(args)
            except (LlmToolError, ValidationError, ValueError) as exc:
                if attempt == self._max_retries:
                    raise
                note = f"上次输出未通过校验（{type(exc).__name__}），请重新生成合法结构。"
        raise AssertionError("unreachable")

    @staticmethod
    def _messages(state: AgentState, note: str) -> list:
        texts = [utterance.text for utterance in state.get("utterances", [])]
        content = "\n".join(texts)
        if note:
            content = f"{content}\n\n{note}"
        return [SystemMessage(content=_SYSTEM), HumanMessage(content=content)]
