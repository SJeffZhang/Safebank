# 任务书 W2：读代理、参数来源与污点

分支 `feat/l2-provenance`。先完整阅读并遵守 `docs/plans/briefs/wave1-common.md`。

## PRD 章节
§5.0、§5.6（谓词与派生模板绑定）、§7.2（①–④）、§7.4（复述检测的判定口径）、§7.5、§7.6、§9.3（含不可信字段清单）、§9.4（敏感参数）、§9.5 G2 与 ReadEnvelope、§9.8。

## 可以改的文件
`services/gateway/app/api/tools.py`（G2）、`pipeline/contract.py`、`pipeline/entity.py`、`pipeline/provenance.py`、`pipeline/intent.py`、`taint/**`（新建）、`services/gateway/policies/trust.yaml`、`policies/injection_patterns.yaml`（新建）、`policies/tools.yaml`（**只能修正值**）、`tests/gateway/w2/**`、`docs/progress/W2-provenance.md`、Makefile 末尾追加 `test-w2`。

## 交付
1. G2 读代理：从 caller 注入 customer_id；校验参数中每个 ID 归属；经 `L3Client` 调 `/internal/read/{tool}`；包装 ReadEnvelope（`source_ref`、`untrusted_paths` 按 §9.3 标注）；写 `read_records`。P1 只实现了 5 个读工具，其余按契约返回 L3 的拒绝，不要在 gateway 里伪造数据。
2. ① 契约阶段：PlanDraft / AppFormPlanRequest 校验（步数、DAG 拓扑序、`arg_sources` 数组同一 param 不重复、引用格式），额外字段 422。
3. ② 实体阶段：用 `ctx.bank_state()` 核对所有权；解析 `{kind:ref}` 引用（add_payee 已存在同指纹时复用并标 no_op）；用 `policy.registry` 为每步算 ActionKind / 基础等级 / outflow，写入 `ctx.steps`；实体在读取账本中未出现过 → `ENTITY_UNSEEN`（resolver=agent，evidence_refs 给出建议读工具）。
4. ③ 来源与污点：`arg_sources` 逐项核验（utterance 区间、read 指针与版本、step、hint、action）；派生模板 `cancelled_monthly_fees` / `available_less_7d_charges` 重算（后者调用 W3 的预留算法时用替身，签名写进待决由指挥协调）；金额只接受 user / 派生 / 白名单 trusted 来源；TAINTED_SENSITIVE_ARG、UNKNOWN_SENSITIVE_ARG、INJECTION_SUSPECTED、memo / alias 敏感规则；app_form 值级污点。
5. ④ 意图范围：读 `ctx.authorization`（W1 的 `AuthorizationInputs`；W1 合入前用 fixture）中的 normalized_scope；(源账户, 目标指纹, 金额) 多重集合比对；源账户默认规则；周期与谓词集合「等于 / 属于」；保护性动作只升黄。
6. `test_llm_fields_ignored.py`：随机改写 `intents`、`rationale` 不改变结论，改写 `arg_sources` 只能让结论变差。

## 退出条件
`make lint`；`make test-gateway`；`make test-gateway-db`；`make test`；`make test-w2` 全部通过。每个规则代码至少 1 个正例 1 个反例；A2 网关层变体（备注注入、用户复述）在单测层面被拒；②–④ 的输出写入 `ctx`，不直接产出 Decision（Decision 归 W3）。
