# L1-plan-fill 评审报告

**评审对象**: `services/agent/app/llm/plan.py` `PlanAdapter.__call__`
**HEAD**: f2e643c
**日期**: 2026-10-27

## P1: 三阶段串行短路 → 合并

**问题**: `_reference_problems` → `_required_value_problems` → `_semantic_problems` 串行短路。一旦 `_reference_problems` 返回非空列表就立即 `raise ReferenceError`，后续两个校验函数不执行。一次模型回复同时包含 read 引用错误 + 擅填 memo 时，重试反馈只报告第一类错误。

**修复**: 三者都执行，`all_problems = ref_problems + val_problems + sem_problems`，合并后一次 raise。

**核验**: 新测试 `test_plan_feedback_includes_all_problem_categories`：fake 首次回复同时包含 read pointer 值与参数不符 + 无关键词的 meme utterance span。旧代码反馈无 "memo"（失败），修复后 41 passed。

## P2-③: 重试反馈前缀中性化

**问题**: 旧前缀「上次计划的来源引用与给定数据不符」只描述第一类校验，合并后三类型都可能触发。

**修复**: 改为「计划未通过本地校验：」+ 全部问题列表 +「请修正后重新生成。」

## 未覆盖/风险

- `_reference_problems` 的 `_MAX_REFERENCE_PROBLEMS = 5` 截断：极罕见场景下 ref 问题 ≥5 条时可能遮盖 value/semantic 问题。不影响安全（重试后仍会被抓到）。
- 三类校验互相独立（无共享可变状态），并行顺序不影响结果。

## 测试结果

```
tests/agent/test_llm_adapters.py: 41 passed
```