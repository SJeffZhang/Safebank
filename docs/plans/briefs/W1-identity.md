# 任务书 W1：身份、会话与原话

分支 `feat/l2-identity`。先完整阅读并遵守 `docs/plans/briefs/wave1-common.md`。

## PRD 章节
§3.2、§5.6（谓词表）、§7.3、§7.4、§7.6、§9.1、§9.5 中 G1、G3、G14、G15、G18、G19、G20、G21、G22、G22a 与 UtteranceRef、§9.8。

## 可以改的文件
`services/gateway/app/auth/tokens.py`（签发、续签、撤销检查）、`auth/pin.py`（新建）、`api/auth.py`、`api/sessions.py`、`utterance/**`（新建）、`services/gateway/policies/intent_lexicon.yaml`（新建）、`tests/gateway/w1/**`、`docs/progress/W1-identity.md`、Makefile 末尾追加 `test-w1`。

## 交付
1. JWT 签发：app JWT（aud=gateway-app，scope [app, approve]，2 小时）与 assistant JWT（aud=safebank-assistant，scope [assistant]，sid，`utts`，10 分钟）；撤销检查（logout 后会话失效）。
2. G1 登录：PIN 用 argon2 哈希校验；同一 customer_id 连续 5 次错误锁 15 分钟（wall）；同一来源 IP 每分钟 ≤ 10 次。G14 登出（撤销该客户会话、作废未执行审批——审批作废通过 `state.transition`）。G15 新建会话。G18（令牌 `utts=[]`，只能读，不延长期限）。G22（仅 DEMO / EVAL 注册生效）。
3. G3 原话登记：kind 互斥校验；确定性意图解析（`intent_lexicon.yaml`：动作词、金额与对象关联、撤销词、改口词、周期词、拼音近音、谓词）；引号 / 书名号 / 疑问句不产生写作用域；收款人「精确且唯一」优先、同音近形建 clarification；复述检测（≥ 8 位账号 vs 客户 L3 不可信数据——先核对 P1 的 `/internal/snapshot` 或读工具能否给出近 90 天 memo / counterparty，不能则只用会话 `read_records` 并在待决登记缺口）；替代（SUPERSEDED）与取消（authorization_epoch）；澄清闭环（`pending_clarifications`、只接受显式 `clarification_id` 且只含缺槽值的补答、`parse_status`）；谓词求值（闲置订阅、稳健型、可疑卡——依赖的 L3 读工具 P1 未实现的，按契约用 fake 测试并登记）。
4. G19（FORM / CLARIFICATION 两种 kind 的字段规则）、G20、G21、G22a。
5. 提供 `utterance.loader.load_for_plan(conn, sid, ids, caller) -> AuthorizationInputs`，供 W2 / W3 使用（W2 在你合入前用 fixture）。
6. 补齐 `tests/gateway/w1/test_auth_matrix.py`：矩阵中每条路由 × 每种凭证组合 × 跨客户，断言 401 / 403 / 200 的预期。

## 退出条件
`make lint`；`make test-gateway`；`make test-gateway-db`；`make test`；`make test-w1` 全部通过。意图解析用表驱动测试，至少覆盖：PRD §12 七幕的原话、X-1~X-4、A2 的两个复述变体、引号与疑问句、改口、取消、王五 / 王武同音。澄清合槽与「普通聊天消息串接成授权」攻击各有测试。
