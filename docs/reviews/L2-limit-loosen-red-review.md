# L2-limit-loosen-red 独立只读评审

## Verdict

**APPROVE**

方向判定、聚合、RED→OTP 链路均正确，未发现功能缺陷或安全弱化；以下 2 条 P2 为测试覆盖缺口，不阻塞合并。

## Findings (P0,P1,P2)

- **P0**：无。
- **P1**：无。
- **P2-1**：`update_card_limits` 的「混合编辑」场景（单笔上调 + 单日下调 → 仍判 LOOSEN→RED）与 `update_card_controls` 的「一通道开 + 另一通道关 → RED」没有直接回归测试。`action_kind`（registry.py:106-116）用 `any(...)` 语义已正确处理该场景，但这是本变更依赖的既有逻辑，建议补一条测试锁定。
- **P2-2**：没有测试直接断言「LOOSEN 计划的 `build_view(...).otp_required is True`」。本变更是**首次**出现「base_risk 升为 RED 而工具自身 `otp_required=false`」的组合（tools.yaml 中 RED 工具均显式 `otp_required: true`），`planview.py:390` 的 `risk is Risk.RED` 分支因此首次成为 OTP 的唯一来源；代码正确，但缺少回归测试证明该链路。

## Checks

1. **分类正确（limit 升/降、渠道开/关）** — 通过。`action_kind`（registry.py:106-116）：`update_card_limits` 对 `single_limit_cents`/`daily_limit_cents` 任一字段 `values[field] is not None and > current` 即判 LOOSEN；`update_card_controls` 对任一渠道 `is True and current False` 即判 LOOSEN。`step_base_risk`（registry.py:157-159）仅对 `CARD_LIMIT_LOOSEN`/`CARD_CONTROL_LOOSEN` 升 RED，否则回落 `policy(tool).base_risk`。
2. **混合编辑（升单笔 + 降单日 = loosen）** — 逻辑正确。`any` 语义下只要单笔字段上涨即 LOOSEN，单日下调不影响结论（P2-1：缺直接测试）。
3. **多步计划聚合** — 通过。`DecisionStage._aggregate`（decision.py:114）`max(_RISK_ORDER.get(s.base_risk))`，任一 RED 步即整体 RED；测试 `test_mixed_plan_with_loosen_step_aggregates_red` 以 transfer(YELLOW)+限额上调(RED) 断言 REQUIRE_APPROVAL + RED。
4. **两个工具均覆盖** — 通过。`update_card_limits`（限4条测试）与 `update_card_controls`（渠道测试）各测升/降两方向。
5. **RED 确实触发 otp_required** — 代码通过。`planview.py:390`：`otp_required = any(policy(s.tool).otp_required for s in ctx.steps) or risk is Risk.RED`；LOOSEN 工具 `otp_required=false`（tools.yaml:52/60），故 OTP 完全由 `risk is Risk.RED` 提供。`Risk` 为枚举，`Risk("RED") is Risk.RED` 成立（测试中 `assert risk is Risk.RED` 已证）。（P2-2：缺 `build_view` 层测试。）
6. **未弱化其它路径** — 通过。`step_base_risk` 仅对 LOOSEN 升级，TIGHTEN/非卡工具一律回落 `policy(tool).base_risk`，与改动前 `rule.base_risk`（`rule = policy(step.tool)`，entity.py:60）逐值相同。`entity.py:70` 是唯一构造 `ResolvedStep.base_risk` 的位置（grep 全库确认），无其它路径持有旧值。`action_kind` 已在 entity.py:63 先行调用，`step_base_risk` 二次调用不会引入新的 `LookupError` 失败面。
7. **测试命令** — `.venv/bin/pytest tests/gateway/test_policy.py -q` → **12 passed**（1.08s）。
8. **测试有效性** — 基本有效。方向判定 4 条断言具体值（"RED"/"YELLOW"），聚合 1 条走真实 `_aggregate` 而非 mock；`action_kind` 的 `any` 语义与 `Risk` 枚举恒等均为真实路径。缺口见 P2-1/P2-2。
