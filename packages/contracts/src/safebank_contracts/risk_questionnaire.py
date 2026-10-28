"""Static rq_v1 risk questionnaire — versioned text, never from LLM.

PRD §5.5 / §8.2: six questions, option IDs a/b/c/d, scores 1–4 increasing
with risk tolerance.  Total 6–24 → R1:6–9  R2:10–13  R3:14–17  R4:18–21  R5:22–24.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Literal

OptionId = Literal["a", "b", "c", "d"]
QuestionId = Literal["q01", "q02", "q03", "q04", "q05", "q06"]


class RqOption:
    __slots__ = ("id", "label", "score")

    def __init__(self, id: str, label: str, score: int) -> None:
        self.id = id
        self.label = label
        self.score = score


class RqQuestion:
    __slots__ = ("id", "prompt", "options")

    def __init__(self, id: str, prompt: str, options: tuple[RqOption, ...]) -> None:
        self.id = id
        self.prompt = prompt
        self.options = options


# ── rq_v1 ──────────────────────────────────────────────────────────────
_rq_v1_questions: tuple[RqQuestion, ...] = (
    RqQuestion(
        id="q01",
        prompt="您计划投资多长时间？",
        options=(
            RqOption("a", "不到 1 年", 1),
            RqOption("b", "1–3 年", 2),
            RqOption("c", "3–5 年", 3),
            RqOption("d", "5 年以上", 4),
        ),
    ),
    RqQuestion(
        id="q02",
        prompt="您的收入来源稳定性如何？",
        options=(
            RqOption("a", "不稳定，有失业或断收风险", 1),
            RqOption("b", "基本稳定，偶尔波动", 2),
            RqOption("c", "稳定，有持续工资收入", 3),
            RqOption("d", "非常稳定，有多重收入来源", 4),
        ),
    ),
    RqQuestion(
        id="q03",
        prompt="您的应急储备相当于多少个月的生活支出？",
        options=(
            RqOption("a", "不足 3 个月", 1),
            RqOption("b", "3–6 个月", 2),
            RqOption("c", "6–12 个月", 3),
            RqOption("d", "超过 12 个月", 4),
        ),
    ),
    RqQuestion(
        id="q04",
        prompt="您对以下哪类投资产品有实际经验？",
        options=(
            RqOption("a", "仅存款或货币基金", 1),
            RqOption("b", "债券或固定收益产品", 2),
            RqOption("c", "混合型基金", 3),
            RqOption("d", "股票或衍生品", 4),
        ),
    ),
    RqQuestion(
        id="q05",
        prompt="您可以接受的最大投资损失是多少？",
        options=(
            RqOption("a", "不能接受任何本金损失", 1),
            RqOption("b", "本金损失不超过 5%", 2),
            RqOption("c", "本金损失不超过 15%", 3),
            RqOption("d", "本金损失超过 15% 也可以接受", 4),
        ),
    ),
    RqQuestion(
        id="q06",
        prompt="当您的投资亏损超过 20% 时，您会怎么做？",
        options=(
            RqOption("a", "立即全部赎回", 1),
            RqOption("b", "部分赎回，转为保守产品", 2),
            RqOption("c", "继续持有，等待市场恢复", 3),
            RqOption("d", "逢低加仓", 4),
        ),
    ),
)

QUESTIONNAIRE = MappingProxyType(
    {
        "rq_v1": MappingProxyType({q.id: q for q in _rq_v1_questions}),
    }
)

QUESTION_IDS: tuple[QuestionId, ...] = tuple(q.id for q in _rq_v1_questions)  # type: ignore[assignment]

# Score → risk level (R1–R5)
SCORE_RANGES: MappingProxyType[int, tuple[int, int]] = MappingProxyType(
    {1: (6, 9), 2: (10, 13), 3: (14, 17), 4: (18, 21), 5: (22, 24)}
)

VALIDITY_MONTHS: Literal[12] = 12
