# E0b T6 逐样本证据独立审

**审核范围**：`6653010..74f4c03`（`evaluation/metrics/compute.py`、`evaluation/runner/loop.py`、`tests/evaluation/runner/test_case_evidence.py`）

**方法**：纯静态代码审阅 + 协调者亲跑 45 passed 确认。未跑真实 DB / Docker / live。

**结论**：**REQUEST_CHANGES** — P1 ×4（tuple 绕过、中段 Bearer 绕过、中段 JWT 绕过、`g4_decision`/`g4_findings` 提取即丢弃），P2 ×5。

---

## P1 — 阻塞合并

### P1-1 `_strip_secrets` tuple 绕过

**文件**：`evaluation/metrics/compute.py:35–62`

`_strip_secrets` 的类型分支仅覆盖 `dict`、`list`、`str`，**元组不匹配任何分支，原样返回**。HTTP headers 列表中的元组 `("Authorization", "Bearer eyJ...")` 因此完整泄露。

**精确最小例**：
```python
assert _strip_secrets({"headers": [("Authorization", "Bearer eyJhbGci.eyJzdWI.SflKxw")]}) \
    == {"headers": [("[REDACTED]", "[REDACTED]")]}
# 实际：{"headers": [("Authorization", "Bearer eyJhbGci.eyJzdWI.SflKxw")]}
```

**建议**：新增 `isinstance(obj, tuple)` 分支，递归 strip 每个元素。

---

### P1-2 `_strip_secrets` 中段 Bearer 绕过

**文件**：`evaluation/metrics/compute.py:56`

`obj.startswith("Bearer ")` 只匹配**以 Bearer 开头的字符串**。以下形式全部绕过：

- `"Authorization: Bearer eyJ..."`（前面有 header 名）
- `"X-Bearer eyJ..."`（前面有其他字符）
- `"bearer eyJ..."`（小写）

**精确最小例**：
```python
obj = {"header": "Authorization: Bearer eyJhbGci.eyJzdWI.SflKxw"}
assert _strip_secrets(obj)["header"] == "[REDACTED]"
# 实际："Authorization: Bearer eyJhbGci.eyJzdWI.SflKxw"
```

**建议**：改为 `re.search(r'\bBearer\s+\S+', obj, re.IGNORECASE)` 或至少 `"Bearer " in obj`。

---

### P1-3 `_strip_secrets` 中段 JWT 绕过

**文件**：`evaluation/metrics/compute.py:58`

`_JWT_RE.match(obj)` 带锚点 `^...$`，只匹配**整串恰为 JWT** 的字符串。嵌入长文本中间的 JWT 完全绕过。

**精确最小例**：
```python
jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
assert _strip_secrets("some text " + jwt + " more") == "[REDACTED]"
# 实际："some text eyJ... more"
```

**建议**：去掉 `^$` 锚点，改用 `_JWT_RE.search(obj)`。考虑到 JWT 的三段点分结构特征性强，假阳概率低；若担心，可只在 `len(obj) > 100` 时长字符串上做 search。

---

### P1-4 `g4_decision` 与 `g4_findings` 提取即丢弃

**文件**：`evaluation/metrics/compute.py:85–143`（提取）、`evaluation/runner/loop.py:230`（使用）

`extract_g4_evidence` 返回的 dict 中含有 `g4_decision`、`g4_findings`、`g4_attempts`、`plan_draft` 四个键。但 `run_corpus` 只取 `plan_draft` 和 `g4_attempts` 写入 `CaseRun`：

```python
# loop.py:226–229
evidence = extract_g4_evidence(transport.requests)
...
plan_draft=evidence["plan_draft"],
g4_attempts=evidence["g4_attempts"],
# ← g4_decision, g4_findings 丢弃！
```

`CaseRun` 数据类中没有 `g4_decision`/`g4_findings` 字段，`write_outputs` 自然写不出去。最后一个成功 G4 调用的 decision 与 findings 只存于 `g4_attempts[-1]` 的嵌套 dict 中——但如果所有 attempt 均失败（均为 503/422），`g4_attempts` 中没有任何 `decision`，而 `extract_g4_evidence` 返回的顶层 `g4_decision=None` 仍录音了"无成功决策"这一事实——该事实丢失。

**建议**：方案 A（推荐）：在 `CaseRun` 新增 `g4_decision: str | None = None` 与 `g4_findings: list[dict] | None = None`，`run_corpus` 传入，`write_outputs` 写出。方案 B：若认为 `g4_attempts` 已包含所有信息，则从 `extract_g4_evidence` 的返回值与 `CaseRun` 中删除 `g4_decision`/`g4_findings`，避免死代码与误解。

---

## P2 — 不阻塞合并，建议修复

### P2-1 `_strip_secrets` 深度绕过

**文件**：`evaluation/metrics/compute.py:38–39`

```python
if _depth > _MAX_STRIP_DEPTH:
    return obj  # 深度 >10 的嵌套原样返回，不 strip
```

如果 plan_draft 或 g4_attempts 的嵌套深度偶然超过 10，深层的 `api_key`/`token` 字段不会被脱敏。概率低，但作为安全防线不应有地板门。

**建议**：超过深度时继续递归但不再深入（`return "[DEEP]"` 而非 `return obj`），或提高 `_MAX_STRIP_DEPTH` 到 20 并记录 warning。

---

### P2-2 `startswith("Bearer ")` 大小写敏感

测试覆盖只有 `"Bearer "`（大写 B），但 HTTP 规范中 token68 的 auth-scheme 虽区分大小写，现实中有 `bearer` 小写的非标准用法。

**建议**：P1-2 改为 `re.search` 时自然解决；如果保留 `startswith`，加 `obj.lower().startswith("bearer ")`。

---

### P2-3 `_strip_secrets` set 类型未处理

与 tuple 同理，set 不匹配 dict/list/str，原样返回。包含 `"Bearer xyz"` 的 set 不会被脱敏。

**建议**：新增 `isinstance(obj, (tuple, set, frozenset))` 分支，返回同类型容器 + 递归 strip。

---

### P2-4 `extract_g4_evidence` 中 `plan_draft` 提取逻辑晦涩

```python
pd = req_body.get("plan_draft", req_body) if "plan_draft" in req_body else req_body
```

`"plan_draft" in req_body` 决定查找键，但无论走哪条分支，**最终 fallback 都是 `req_body` 本身**（若 plan_draft 键存在但值为 None，`.get("plan_draft", req_body)` 返回 `req_body`；若键不存在，`else req_body` 也返回 `req_body`）。虽然 `isinstance(pd, dict) and "steps" in pd` 做了最终守卫，但

- 含义不直观：代码读起来像"如果请求体本身就是 plan（没有外层的 plan_draft 包装），就用请求体"
- 如果产品代码改动了 G4 请求体形状（例如加了新顶层键），`req_body` 可能碰巧符合 `"steps" in pd` 而被误存为 plan_draft

**建议**：简化为：
```python
raw = req_body.get("plan_draft") if isinstance(req_body, dict) else None
if isinstance(raw, dict) and "steps" in raw:
    plan_draft = raw
```
不去碰 `req_body` 本身。

---

### P2-5 `records_assert_field` 测试盲点

现有测试 `test_secrets_stripped_in_jsonl` 只在 `plan_draft` 顶层注入 `assistant_token` 和 `steps[0].args.api_key`，加上 `g4_attempts[0]` 中注入 `authorization`。**缺失案例**：

- g4_attempts 中 `error_code` 为字符串 Bearer token（503 时的 error body 可能含 token）
- plan_draft 嵌套在 `arg_sources[].source.memo` 等深层字段中的 token
- 用户 utterance 文本中含 JWT 样式的字符串（边界场景，但 utterances 也应经过 strip——当前 utterances 是纯文本列表，`_strip_secrets` 对 list 递归但 entry 是短字符串不会被 JWT 正则命中）

**建议**：补参数化案例覆盖上述路径。utterances 中嵌入 JWT 的情况可作已知限制标注。

---

## 正面发现（已正确实现）

1. **`g4_attempts` 全部记录**：422/503/200 每次调用都保留，不遗漏失败的诊断信息。✓
2. **`plan_draft` 完整保留**：多 step 的 arg_sources、depends_on 全部进 evidence。✓
3. **向后兼容**：`CaseRun` 新字段全部有默认值，旧构造代码不受影响；现 17 项指标测试结果不变（`test_existing_17_metric_tests_stable` 通过）。✓
4. **不修改调用方数据**：`_strip_secrets` 返回新 dict，`extract_g4_evidence` 只读 transport 数据。✓
5. **密钥键名覆盖全面**：`token|key|secret|password|pin|bearer|jwt|auth|credential`，用 `search` 匹配子串。✓
6. **usage_known/usage 成对**：`usage_known=False` 时 `usage=None`，不编造未知数据。✓
7. **`run_corpus` 接线完整**：`utt_texts` 逐 turn 收集，`extract_g4_evidence(transport.requests)` 在每个 case 结束时调用，`CaseRun` 正确接收。✓
8. **深度保护**：`_MAX_STRIP_DEPTH=10` 防止无限递归（尽管回退策略需修正）。✓
9. **`_strip_secrets` 纯函数**：无副作用，可独立测试。
10. **旧字段不变**：`tokens`、`latency_ms`、`finding_codes` 等仍在 JSONL 中。
11. **meta 不入 cases.jsonl**：metrics.json 与 cases.jsonl 分离正确。

---

## 修复顺序建议

1. P1-1（tuple）→ 1 行 `elif isinstance(obj, tuple): return tuple(_strip_secrets(v, _depth=_depth+1) for v in obj)`
2. P1-2（中段 Bearer）→ `"Bearer " in obj` 最低成本，或 `re.search`
3. P1-3（中段 JWT）→ 去锚点 `_JWT_RE.search(obj)`
4. P1-4（g4_decision/findings 丢弃）→ 方案 A 加字段
5. P2-1→2→3→4→5 按顺序

每条修复后补对应红测（先写失败用例，再修代码转绿）。

---

**审核人**：DeepSeek V4 Pro（全新会话，只读）  
**审核时间**：2026-10-26  
**实际运行**：`make test` 未跑（仅协调者亲跑 `test_metrics + test_case_evidence = 45 passed`）；tuple/JWT/Bearer 绕过证据为静态分析 + 手动 Python 验证（`/tmp/sbg-probe-strip.py`）
---

## 修复复审（74f4c03..57a08e2）

**方法**：只读 `git diff 74f4c03..57a08e2` + 协调者实测 `test_metrics + test_case_evidence = 56 passed`（57a08e2）。不做探针、不读其他文件、不跑 Docker/live。

### 逐条结论

| # | 条目 | 结论 | 备注 |
|---|---|---|---|
| P1-1 | `_strip_secrets` tuple 绕过 | **ADDRESSED** ✓ | 新增 `isinstance(obj, tuple)` 分支；`test_strips_tuple_headers` 验证 tuple 内 Bearer 被 strip、header name 保留 |
| P1-2 | 中段 Bearer 绕过 | **ADDRESSED** ✓ | `startswith("Bearer ")` → `_BEARER_RE.search(obj)` + `re.IGNORECASE`；`test_strips_bearer_anywhere_in_string` + `test_strips_lowercase_bearer` |
| P1-3 | 中段 JWT 绕过 | **ADDRESSED** ✓ | 去 `^$` 锚点、`match` → `search`；`test_strips_embedded_jwt` |
| P1-4 | `g4_decision`/`g4_findings` 丢弃 | **ADDRESSED** ✓ | `CaseRun` 新增两字段（默认 `None`）、`run_corpus` 传入、`write_outputs` 写出；`test_g4_decision_and_findings_in_jsonl` + `test_g4_decision_none_when_no_successful_g4` |
| P2-1 | depth 绕过 `return obj` | **ADDRESSED** ✓ | `return "[REDACTED:depth]"` 替代 `return obj`；`test_depth_limit_returns_placeholder` |
| P2-2 | Bearer 大小写 | **ADDRESSED** ✓ | 由 P1-2 的 `_BEARER_RE` + `re.IGNORECASE` 自然解决；`test_strips_lowercase_bearer` |
| P2-3 | set/frozenset 未处理 | **ADDRESSED** ✓ | 新增 `isinstance(obj, (set, frozenset))` 分支；`test_strips_set` + `test_strips_frozenset` |
| P2-4 | `extract_g4_evidence` plan_draft 逻辑晦涩 | **ADDRESSED** ✓ | 简化为 `req_body.get("plan_draft")` + `isinstance(raw, dict) and "steps" in raw`，不再 fallback 到 `req_body` 本身 |
| P2-5 | 测试盲点 | **ADDRESSED** ✓ | `test_strips_bearer_in_g4_error_code` + `test_strips_secrets_in_nested_arg_sources`；utterances 中 JWT 样式文本已在原始评审中标注为「已知限制」，不阻塞 |

### 新增问题检查

| 检查项 | 结论 |
|---|---|
| Bearer 正则 `\bBearer\s+\S+` 过度替换 | **安全**：整串含 Bearer 时返回 `[REDACTED]`，丢失前缀文本，但评测输出以安全为优先，不保留部分原文 |
| JWT 无锚点假阳风险 | **可接受**：`len > 50` 守卫仍在；评测输出中 `xxx.yyy.zzz` 恰好 >50 字符的假阳概率低；原始评审已明确批准此权衡 |
| set 类型返回与 JSON 序列化 | **安全**：`_strip_secrets` 返回的 `set` 仅出现在单元测试；实际 `g4_attempts`/`plan_draft` 来自 JSON 解析，只有 `list`/`dict` |
| `g4_findings=None` 时 `_strip_secrets(None)` | **安全**：`_strip_secrets(None)` 走末尾 `return obj`，输出 JSON `null`，合法 |
| `g4_decision`/`g4_findings` 向后兼容 | **安全**：两者默认 `None`，旧 `CaseRun(...)` 构造不受影响；旧 `write_outputs` 消费者忽略未知 JSON 键 |
| `[REDACTED:depth]` 格式 | **安全**：纯标记值，不含任何凭据特征 |

### 协调者实测确认

- `test_metrics + test_case_evidence`：**56 passed**（57a08e2）
- 换回 74f4c03 的 `compute.py` 后新增 9 条全部失败 → **红测有效**（协调者验证）

### 结论

**APPROVE** — 9 条全部 ADDRESSED，无新增 P0/P1/P2，修复不引入回归。

---

**复审人**：DeepSeek V4 Pro（全新会话，定点复审）  
**复审时间**：2026-10-27  
**实际运行**：`test_metrics + test_case_evidence = 56 passed`（协调者亲跑）
