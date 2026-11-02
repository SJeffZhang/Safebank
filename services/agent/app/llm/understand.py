"""``report_understanding`` adapter: propose intents/slots/needs_write, never authorize."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from services.agent.app.graph.state import AgentContext, AgentState, Understanding, UtteranceKind
from services.agent.app.llm._common import LlmToolError, single_tool_call
from services.agent.app.llm._cross_scene_reads import current_x3_selection
from services.agent.app.llm.schema import UNDERSTANDING_TOOL, understanding_tool

_SYSTEM = (
    "你是银行助手，只把用户请求解析为结构化理解，不执行、不授权任何操作。"
    "intents 是意图列表；slots 是槽位数组（intent_index/name/value/span）；"
    "needs_write 表示该请求是否需要写操作。不要在这里估算或决定金额。"
    "span使用原文从0开始的字符区间[start,end)，必须满足0<=start<end；"
    "不要用[0,0]占位，不需要或无法定位的槽位应省略。"
    "明确的聚餐AA收款指令需要创建create_split_request，needs_write=true；"
    "仅询问每人多少钱、计算分摊或查看已有AA进度是只读，不能因此创建收款请求。"
)


# A selector for the observed G3-supported full sentence, not an authorization parser.
# Other expense/time synonyms and omitted-self forms remain outside this repair.
_AA_COLLECTION = re.compile(
    r"昨晚聚餐[0-9]+(?:\.[0-9]{1,2})?元[，,]我和"
    r"[\u4e00-\u9fff]{1,20}(?:、[\u4e00-\u9fff]{1,20}){0,19}AA[。.!！]?"
)
_AA_RETRY_NOTE = (
    "上次理解未通过语义校验：该完整聚餐AA收款指令需要create_split_request写计划，"
    "needs_write应为true，不能仅作为计算或进度查询。请重新生成理解，"
    "保留用户原意，不在此处生成或更改金额、参与人、来源及执行参数；授权仍由L2判定。"
)


_SELECTION_RETRY_NOTE = (
    "上次理解未通过语义校验：当前G20绑定的已完成产品选择包含FUND_SUBSCRIBE写请求，"
    "不能作为仅查询处理。请结合结构化选择重新生成理解，不更改目标、金额、来源或权限；"
    "授权、适当性和确认仍由L2决定。"
)


class _SelectionWriteMismatch(LlmToolError):
    """A current structured G20 selection was incorrectly treated as read-only."""


class _AAWriteMismatch(LlmToolError):
    """An explicit scoped AA command was incorrectly classified as read-only."""


def _structure_retry_note(exc: Exception) -> str:
    # Error locations may contain arbitrary extra-key names. Only emit fields
    # from our schema, never exception text, input values or validator context.
    locations = []
    if isinstance(exc, ValidationError):
        fields = {
            "intents",
            "needs_write",
            "unclear",
            "slots",
            "intent_index",
            "name",
            "value",
            "span",
        }
        for error in exc.errors(include_input=False, include_url=False)[:5]:
            path = [
                str(part)
                for part in error["loc"]
                if (type(part) is int and 0 <= part < 1000)
                or (isinstance(part, str) and part in fields)
            ]
            # Slot's after-validator checks its span rather than the text value.
            if len(path) == 2 and path[0] == "slots" and error["type"] == "value_error":
                path.append("span")
            if path:
                locations.append(".".join(path))
    details = "；".join(locations) or "report_understanding结构"
    return (
        f"上次输出未通过结构校验：{details}。请按Schema重新生成；"
        "span是原文0起始字符区间[start,end)，0<=start<end，不得用[0,0]占位；"
        "无法定位或不必要的槽位请省略，不得编造原文或改写用户意图。"
    )


def _scoped_aa_collection(state: AgentState) -> bool:
    utterances = state.get("utterances", [])
    return (
        state.get("parse_status") == "SCOPED"
        and state.get("clarification") is None
        and state.get("clarification_id") is None
        and len(utterances) == 1
        and utterances[0].kind is UtteranceKind.TYPED
        and _AA_COLLECTION.fullmatch(utterances[0].text.strip()) is not None
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
        selection = current_x3_selection(state, context)
        for attempt in range(self._max_retries + 1):
            message = self._model.invoke(self._messages(state, note, selection))
            if self._observer is not None:
                self._observer(message)
            try:
                args = single_tool_call(message, UNDERSTANDING_TOOL)
                result = Understanding.model_validate(args)
                if not result.needs_write and selection is not None:
                    raise _SelectionWriteMismatch("当前G20产品选择被误判为只读")
                if not result.needs_write and _scoped_aa_collection(state):
                    raise _AAWriteMismatch("AA collection incorrectly classified as read-only")
                return result
            except (LlmToolError, ValidationError, ValueError) as exc:
                if attempt == self._max_retries:
                    raise
                note = (
                    _AA_RETRY_NOTE
                    if isinstance(exc, _AAWriteMismatch)
                    else _SELECTION_RETRY_NOTE
                    if isinstance(exc, _SelectionWriteMismatch)
                    else _structure_retry_note(exc)
                )
        raise AssertionError("unreachable")

    @staticmethod
    def _messages(state: AgentState, note: str, selection: dict | None = None) -> list:
        texts = [utterance.text for utterance in state.get("utterances", [])]
        content = "\n".join(texts)
        if selection is not None:
            content += "\n当前G20绑定的结构化选择，仅供理解上下文，不能代替实际读取或L2授权：\n"
            content += json.dumps({"current_g20_selection": selection}, ensure_ascii=False)
        if note:
            content = f"{content}\n\n{note}"
        return [SystemMessage(content=_SYSTEM), HumanMessage(content=content)]
