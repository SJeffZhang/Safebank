# W2 memo / alias 敏感内容黄级增量独立只读评审

日期：2026-10-24。范围：`git diff 50c1bdb 8063f27`（feat/l2-provenance，HEAD 8063f27）。只读评审，唯一写入本报告。未运行 pytest（本 worktree 无 `.venv`），结论基于源码静态核对 + 协调者独立全量复跑记录；金额/数字正则边界由 `taint/text.py` 与 `provenance.py` 源码直接判定。

## 逐点核对

1. **只升级不放宽** ✓：唯一业务改动在 `services/gateway/app/pipeline/provenance.py:215–221`，把原 `pass` 占位换成 `ctx.add(Code.MEMO_SENSITIVE_CONTENT, FindingEffect.RISK_YELLOW, step_id=...)`。trust 判定（含 provenance.py:196–197 的 memo/alias 来源边界、untrusted→`TAINTED_SENSITIVE_ARG/DENY`、unknown→`UNKNOWN_SENSITIVE_ARG/REPLAN,user`）完全未动。YELLOW 是**追加**，与 DENY/REPLAN 共存，不替换、不降级。read（含 trusted read）仍强制 untrusted→DENY；无有效来源仍 REPLAN。✓
2. **NFKC / 7-8 位边界 / 金额格式** ✓：`numbers()`（`taint/text.py`）先 NFKC+去空白再 `[0-9]{8,}`，覆盖全角 8 位与空格分隔 8 位，7 位不触发。`_MONEY_PATTERN`（provenance.py:28–32）对 `normalize(value)`（NFKC+去空白+去 Cf）匹配：数字+元/人民币/CNY/RMB、人民币/CNY/RMB+数字、¥/￥ 及大小写；相对改动前死代码新增 RMB、前缀形式与全角归一，旧有匹配（数字+元/人民币/CNY、¥/￥）无一被放宽。✓
3. **alias 来源边界** ✓：memo/alias 仅允许 utterance/action（provenance.py:196–197），其余 kind 一律降 untrusted——本轮未改。transfer/create_split_request 的 `memo` 与 add_payee 的 `alias` 均在 `policies/tools.yaml` 的 `sensitive_params`。✓
4. **xfail 真修复** ✓：`tests/gateway/w2/test_provenance.py` 移除 `@pytest.mark.xfail(strict=True)` 及随之无用的 `import pytest`，原测试变为普通断言（`f.code.value == "MEMO_SENSITIVE_CONTENT" and f.effect.value == "RISK_YELLOW"`），非隐藏失败。✓
5. **只改 W2 所属文件** ✓：diff 仅 `pipeline/provenance.py`（W2 清单内）、`tests/gateway/w2/test_provenance.py`、新增 `tests/gateway/w2/test_memo_sensitive.py`、`docs/progress/W2-provenance.md`；未触碰 contracts/bankcore/bank-backend/migrations/registry/tools.yaml/PRD/AGENTS 等冻结区。✓
6. **测试有效** ✓：test_memo_sensitive.py 75 项（3 工具 × [15 正例 + 5 反例 + 2×2 来源 + 3 action]）+ test_provenance.py 7 项 = 82，与进度自报一致；反例含 7 位、普通文本、空串、None；read/unknown 来源断言 DENY/REPLAN 与 YELLOW 并存；action 用例 monkeypatch `authorization_evidence` 且形状与 `AuthorizationEvidence.sources` 一致。

## 发现

P0：无。
P1：无。

P2-1 `_MONEY_PATTERN` 第三分支 `[¥￥]`（provenance.py:31）不要求数字：任何含 ¥/￥ 的 memo/alias，即使无金额也升黄（例：「汇率参考￥」→ RISK_YELLOW，可把 GREEN 的 create_split_request 推黄需审批）。方向保守（只多警示、无安全危害），但会无谓增加正常任务摩擦，且测试无「裸符号无数字」反例钉住该边界。建议改为要求邻近数字（如 `[¥￥](?=[0-9])|[0-9][¥￥]`）。注：此分支继承自改动前死代码的 `|[¥￥]`，非本轮引入，但本轮首次生效。

## 未确认

- 本 worktree 无 `.venv`，无法亲自定点跑 pytest；测试有效性与通过数依据源码静态核对、进度自报（75+7=82）与协调者独立全量复跑。进度文档红测计数「58 failed / 21 passed」与 82 总数不严格吻合（或为个别 error/xfail 计数差异），仅文档措辞，不影响代码结论。
- 金额格式的 3 位小数（如「100.123元」不匹配）与 `matches()` 的 2 位小数口径一致，属既有约定，未列为发现。

结论：**APPROVE**。P0/P1 无；P2-1 为建议性，不阻塞。
