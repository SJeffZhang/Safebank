# November 1 T5 hint publication safety review

**APPROVE：本结论覆盖精确bee711d8138228f915bf66bedef994f2e589f6ed + 两个指定补丁的组合，不覆盖缺G3 hook的裸bee checkpoint。** 没有发现此组合中的可复现授权扩张、跨客户采纳、过期采纳或重复资金执行通路。根节点、来源、scope、父计划/最新evaluation、epoch及TTL均在发布后和采纳前重新检查；FORM不发布hint。真实PG锁/并发与最终集成head由root验收，本次未运行PG。

## 审查对象与可复现边界

- Parent：`d6a5df11d67baaa41c47862c11ab7c24e767f1cf`。
- 精确head：`bee711d8138228f915bf66bedef994f2e589f6ed`，git archive `/private/tmp/sbg-nov1-repairs/review-hints`。
- 在私有archive逐个`git apply --check`并应用 `/private/tmp/sbg-nov1-repairs/t5-service-hook.patch` 与 `t5-legacy-hint-test.patch`；未取root/B其他WIP作为审查对象，未改任何工作树产品文件。
- Hook patch SHA256：`db01af7398cd019bf506eff61612972a79b9547a2980ead71e440aa7605f7cb8`。
- Legacy test patch SHA256：`b2a09ba20b5715157395ec264c95b13b726abd09ad7b8d3b3f5e54ab6352d4b5`。
- 只读源码+合成SQL边界pure，无DB/provider/API/模型/browser/Docker/部署。这里的G4/G22a/G3术语指直接执行生产处理函数/内部函数，不是实际网络服务验收。

## 逐项安全结论

| 边界 | 观察到的确定性实现与独立验证 |
|---|---|
| 发布身份 | `hint_sources.publish_replan_hints`只接受agent origin、A身份、assistant scope、匹配customer/sid、JWT持有全部auth roots；末阶段拿当前session重验epoch并重载父计划和来源。独立5项caller customer/sid/kind/scopes/utts负控均409，未插hint。 |
| FORM | app_form及非REPLAN/无hint先返回空；G4响应使用实际published_hints，未把原decision的FORM proposal透传。独立FORM正控不接conn也不触SQL，返回空。未把虚拟FORMscope伪装成typed原话。 |
| 父计划/当前evaluation | 父plan必须本customer/sid、agent、REPLAN，保存root集合与draft集合一致。重载最新revision的REPLAN evaluation，发布还必须和当前evaluation_id/revision/auth一致。digest绑定draft、plan_hash/steps_hash/request_hash及evaluation内容，修改draft、findings、revision或状态不能继续使用旧hint。 |
| Root/来源证明 | 每个root必须ACTIVE/SCOPED、当前epoch、非echo、aware且不未来/未过期；继承既有`_adopted_sources`遍历真实父链/已采纳source并核scope、source.used_by、sid/epoch/状态与时间。不是调用方自报文本或从任意session补scope。hint绑定保存的root和source内容。 |
| 仅金额收缩 | `_narrowed`逐个比较原scope，保留action/source/target/period/channel/fields及transfer数量顺序，金额为正整数、不扩大，至少一笔严格减小；label由真实分金额重建。原DecisionStage还把真实失败transfer步/原金额/真实target与可执行预演上限绑定；该单元原审查见review-nov1-plan-replan.md。独立period/channel/fields/逐笔source/全无变化负控均拒绝。 |
| Hash/展示 | 64位小写hex digest含session/customer/epoch、保存父plan、evaluation、root/source及hint精确public scope/label/时间；GET不公开view_hash/parent_plan_id/私有绑定。Unicode、大小写替换、null、旧空digest均控制409，不裸异常。此digest为可信DB中服务端发布内容的一致性承诺；授权仍来自L2真实source，LLM和label不授予权限。 |
| TTL | 发布取proposal期限、原root期限、当前wall+10min最小值，必须aware且严格未来；GET/G3重复检查不未来created、到期、10min上限、root上限。相同真实时刻的UTC/+08存取经统一UTC序列化保持绑定；TTL==now明确拒绝。使用wall而非模拟clock。 |
| G22a所有权/竞态 | `session_for`先核caller/JWT撤销与customer，hint查询同时按sid+id。GET只是非授权view，未锁读到的信息不能代替G3；G3在原session/source锁下重新执行完整validator。GET后被改scope/epoch/原话/plan状态或过期，采纳会拒绝。 |
| G3单次/无自动执行 | 指定hook位于读取已锁hint、检查used_by/expiry之后，构造Parsed之前；非法hint在supersede与新utterance insert前失败。合法采纳仅以服务器scope登记新kind=hint utterance并写source.used_by。原request_idempotent保留同请求重放；新请求再用同hint会409。真实service.register独立正控检查scope逐字段相同、source关联、一次used_by、第二次拒绝、无execution_jobs写。金额修改仍需重新G4/G9/审批执行。 |
| Legacy | 无parent计划/空或无效digest的历史手工hint，GET和G3统一拒绝。替换旧PG测试是因为它手工种无绑定source，不能继续当正路径；新的真正G4来源PG正路径是独立替代。没有放宽旧fixture绕过新绑定。 |

## 自动复核

先运行作者文件及新PG收集门：

`PYTHONDONTWRITEBYTECODE=1 GATEWAY_DB_TESTS=0 AGENT_DB_TESTS=0 <clean-own-WT>/.venv/bin/python -m pytest -c pyproject.toml tests/gateway/test_hint_publication_unit.py tests/gateway/w3b/db/test_nov1_hint_publication.py -q -p no:cacheprovider`

结果 **32 passed /8 skipped**；日志 `/private/tmp/sbg-nov1-repairs/review-hints-authors.log`。8项PG被明确跳过，绝不记通过。

独立私有测试 `/private/tmp/sbg-nov1-repairs/test_review_hints.py` 最终42项：31项额外绑定/根/时钟/范围负控，真实service.register合法scope单次正控与4个拒绝前无副作用负控，FORM不发布，5项发布身份负控。最终与作者32项合跑 **74 passed**，日志 `/private/tmp/sbg-nov1-repairs/review-hints-final-pure.log`。

两个测试构造问题已修正，不算产品旧红：初次采纳正控复用作者“仅publication”宽松fixture，遗漏transfer.from_account_id，被原scope_is_complete正确拒绝；补上真实canonical source字段（未mock/放宽scope_is_complete）后正控过。后来5个身份测试错误把dataclass Caller当Pydantic调用model_copy，改用dataclasses.replace后5项全部按409期望通过。最终74均为有效断言。

## 与root真实PG证据的关系

root通知首次真正G4/G22a已过，8个新增G3入口均503；作者已定位该新w3b fixture漏了locks.configure（生产lifespan/W1既有正确配置），将仅修测试接线再由root复跑。本审查不把这组fixture失败当产品旧红，也不建议放松生产锁/校验。作者默认全套4554 passed/1129 skipped仅其checkpoint证据；本次没有重跑整套或实际PG，没有以74pure模拟真实SQL隔离/并发/回滚。

root下一门应保留：真正G4返回→G22a→G3同请求幂等/不同请求单次→采纳scope新G4→G9实际panel、并发两采纳一201一409、篡改后无新utterance/used_by/执行job、旧未绑定hint拒绝。裸bee必须与审过的G3 hook共同集成；最终完整head再跑CI/PG，不扩成本次任何现场金融结算结论。

## Root PG follow-up: green3's final 403 location

Read-only follow-up on root integrated code and `/private/tmp/sbg-nov1-hints-pg-green3.log`; no DB/service call or fixture/product mutation. The run reached **14 passed /1 failed**, but the actual assertion failure is `test_nov1_hint_publication.py:126`, GET `/approvals/{id}`. The preceding next G4 status200 and decision REQUIRE_APPROVAL assertions both passed. This is not a hint-source/next-plan authorization rejection.

Confirmed fixture mismatch: `tests/gateway/w3b/db/conftest.py:265` app_jwt signs only `scope=["app"]`; the new test's prepared frontend headers use that helper, then reuse the same header for G9. `services/gateway/app/auth/matrix.py` requires F_APPROVE (`approve`) for GET `/approvals/{approval_id}`, and auth/deps.py's missing-scope branch returns FORBIDDEN403. Existing `tests/gateway/w4b2b/db/test_g9_get.py:157` explicitly verifies only-app ->403. Evidence and the exact boundary were shared with B and root before further investigation; no duplicate source-chain changes are warranted.

Minimum safe correction is fixture-only: give the G9 positive path a normal frontend JWT with app+approve, and retain the app-only G9 ->403 negative assertion. Do not change matrix, relax required scopes or give assistant callers approve. Subsequent real PG remains root-owned; this note makes no claim about its eventual result.
