# 独立审查：账本事实、分类与多客户范围

目标源提交6b05bf1/97d2b7d/382d9ba/4011d5d，根映射f3ded1f/d044a86/e139ca0/5492615；fixture6e8f327。固定验证快照为已集成上述改动的root 8d0ac42。审查者未实现本单元，不修改被审源码。demo-runner工作树存在其他任务未跟踪文件，已完整保留；测试从git archive固定快照解包到/private/tmp/sbg-ledger-review-8d0ac42执行，排除WIP。

## 结论

**APPROVE（本次代码范围）**。未发现新增资金规则或事实分类阻塞。真实PG仍由协调者运行，此结论不代替完整CI或端到端验收。

## 核查证据

- 实际apply_scheduled_transfer按已解析目标产生P2P_INTERNAL、P2P_EXTERNAL或SELF_TRANSFER，OUT保留真实payee_id/card_id，IN不附带该来源；post_transfer的余额、pending、金额、幂等和PostTransfer/AppendTransaction资金effects规则未改，新增参数仅填事实元数据。
- spending_summary将真实P2P及历史TRANSFER_EXTERNAL计为转出；真实MERCHANT_CHARGE及历史MANDATE、CARD_SPEND计为消费；SELF、工资、基金资金动作不计为消费或对外转出，IN不重复累计。detect_subscriptions同时识别历史/当前扣费，但不将扣费本身当使用。
- 旧SCHEDULED_TRANSFER由真实LedgerTransfer的借贷腿判定，只有当前客户本人credit账户可排除转出；缺失ledger、借方不匹配保持保守。L3 load_bank_state加载多客户账户，但counter分类传入的是当前customer的owner_ids，并只查当前customer流水，不把另一个已装载客户的credit当成本人。
- 独立9组合直接调用生产load_bank_state（以仅返回行的内存连接替代SQL I/O），输入由真实apply_scheduled_transfer产生：peer/self/external × real/missing/wrong-debit旧ledger。只有真实self证据的三计数为0，其余daily_outflow、rolling_outflow、daily_used均10000分；peer不混入当前客户计数。包含双方账户的scope证明OTHER不会因multiscope洗成SELF。独立probe保留在/private/tmp/sbg-ledger-review-8d0ac42/test_review_ledger_independent.py。
- 商户分类从真实merchant_id取得权威MCC/保存category，MCC优先；当前窄目录以外映射到已定义category或其他。D4缺merchant或错误ID时，不用counterparty/memo/name选分类；自由文本仍原样保留在untrusted展示字段。商户扣费产生可被实际消费汇总识别的merchant_id/category。
- 周费折算完全整数：annual=amount_cents*52，monthly=annual//12；月费annual=amount*12。不支持period、bool/float/字符串/负数拒绝，不引入浮点金融值。取消结果/订阅读采用同一normalize_period；后续G9有效授权收敛另见review-approval-material.md。

## 独立运行

- 精确root 8d0ac42快照：tests/bankcore，**476 passed / 0.65s**。日志ledger-review-python.log。
- 上述独立真实loader负控：**9 passed / 0.27s**。日志ledger-review-independent.log。
- tests/obligations_data/test_transaction_fact_reload.py仅collect：**5 collected / 0.25s**，未执行DB。fixture旧producer只在初始追加LedgerTransfer时产生历史code，保留真实账户/金额/不可变SQL约束，没有UPDATE已存在流水假造legacy。日志ledger-review-collect.log。

## 未覆盖

本审查没有运行DB、provider、API、Docker或浏览器，未自称真实PG/并发/三层验收通过；协调者另有真实PG回执。固定快照包含已集成其他修复，但未将未跟踪WIP或其他模块变动纳入本次结论。未改源码、AGENTS、PRD，也未推送或合并。
