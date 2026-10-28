# G4 请求体修复独立评审（46539fc）

范围：`git diff origin/main...46539fc` — A 调用方请求体就是 PlanDraft 本身。

## 结论

**APPROVE**（无 P0/P1/P2）

修复正确、范围最小、断言未被削弱：G4 A 调用方按 PRD §9.5 直接校验 PlanDraft 本体、`request_id` 取自 `plan_draft.request_id`；幂等摘要结构与已存记录兼容；JWT 交叉校验与 F 分支未动；eval 侧垫片删除后证据读取改为直读本体，无新的改写垫片。新增「旧包裹形状 422」测试有效。

## P0

（无）

## P1

（无）

## P2

（无）

## 已核对项

### 1. services/gateway/app/api/plans.py（Read 全文 180–289 + diff）

- **A 调用方体按 PlanDraft 校验**：`plans.py:220` `plan_draft = PlanDraft.model_validate(raw_body)`，直接以请求体为 PlanDraft，符合 PRD §9.5。✓
- **request_id 取自 draft**：`plans.py:224` `request_id: str = plan_draft.request_id`；`PlanDraft` 契约确有必填 `request_id: Id`（`packages/contracts/src/safebank_contracts/plan.py:126`）。✓
- **缺 request_id / 非法体仍 422**：`plans.py:222` 与 `plans.py:226` 均 `CONTRACT_INVALID, 422`。`PlanDraft` 缺 `request_id` 或体非法时 `model_validate` 抛错走 422；`request_id` 为空/非 str 走 422。✓
- **会话/JWT utts 交叉校验未被削弱**：`plans.py:235–241`（`session_id != sid → OWNERSHIP_MISMATCH 404`；`utterance_ids ⊆ caller.utts → AUTHORIZATION_CONSUMED 409`）在 diff 外，未改动。✓
- **幂等摘要结构未变**：`plans.py:229–232` `idempotent_body = {"request_id": …, "plan_draft": plan_draft.model_dump(mode="json")}` 键序与取值来源不变。旧记录成立的前提是旧 `request_id` 恒等于 `plan_draft.request_id` —— 属实：旧 eval 垫片 `l1.py` 写 `request_id: draft.get("request_id")`，L1 客户端 `services/agent/app/gateway/client.py` 发送 `body=draft.model_dump(mode="json")`，两者 request_id 一致，故摘要字节不变、已存幂等记录仍有效。✓
- **F 调用方分支未受影响**：`plans.py:194–200` F 分支在体解析前返回 `DEPENDENCY_UNAVAILABLE 503`，diff 未触碰。✓

### 2. 测试（git diff tests/）

- **网关测试改为发送 PlanDraft 本体**：`tests/gateway/w3b/db/conftest.py` `make_body` 改为直接返回 `plan_draft`（`plan_draft is None` 时用 `make_plan_draft_dict(request_id=…)`，其内含 request_id）；`test_g4_evaluate.py` 各调用点改用 `make_body` 或直接传 `pd`。✓
- **新增旧包裹形状 422 测试**：`test_g4_evaluate.py` `test_evaluate_rejects_wrapped_body_returns_422` 发送 `{"request_id": "req_wrapped", "plan_draft": pd}` 断言 422。该体必然 422：即便 `Contract` 基类 `extra="ignore"`，包裹体缺 `session_id/utterance_ids/intents/steps/rationale` 也必失败；若 `extra="forbid"` 则 `plan_draft` 键即失败。两种语义下均 422，测试有效。✓
- **无删除或放宽断言**：diff 中 `-` 行仅把请求体从旧包裹形状改为本体（如 `json={"request_id":…,"plan_draft":{}} → json={}`、`json={"plan_draft": pd} → json=pd`），所有 422/409/401 断言及其错误码断言均保留；`test_evaluate_missing_request_id_returns_422` 现在更精确（`pd.pop("request_id")` 后直发本体）。`tests/gateway/w1/db/test_s1_runtime.py` 的 `json={}` 变化：注销前断言 `not in (401,403)` 仍成立（`{}` 校验失败得 422），注销后断言 401 成立。✓

### 3. evaluation 垫片与证据读取（git diff evaluation/）

- **l1.py 垫片已删除**：`evaluation/runner/l1.py` 移除把 PlanDraft 包成 `{"plan_draft":…,"request_id":…}` 的改写逻辑，改为直接透传。✓
- **loop.py 证据读取改为本体**：`evaluation/runner/loop.py:162–167` 由 `req.get("plan_draft", req)` 改为直读 `req.get("steps", [])` 并加 `isinstance(list)` 守卫。✓
- **compute.py 证据读取改为本体**：`evaluation/metrics/compute.py:208–214` 由 `req_body.get("plan_draft")` 改为 `req_body` 本身（当 `steps` 为 list 时）。✓
- **无新的「改写请求体」垫片**：grep `plan_draft.*request_id` / `request_id.*plan_draft` 于 `evaluation/ tests/ services/gateway/app/` 无命中；`services/agent/app/gateway/client.py` 发送 `body=draft.model_dump(mode="json")`（本体直发），与新的网关 wire 形状一致。✓
