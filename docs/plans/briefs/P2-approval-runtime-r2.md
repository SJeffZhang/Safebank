# P2审批实例密钥小修r2（新会话30轮）
现WIP三测试协调者亲跑3passed/2.18s，尚未完整验收，原51轮耗尽。不要通读AGENTS/PRD，不用子agent。只做此组件，不改L1。原白名单service.py/plans.py/execution.py/newtest/progress；额外conftest改动不批准，请精确撤回自己在conftest新增override参数的diff（其余既有fixture保留）。两app用现有build_app后设置各自app.state.approval_hmac_key=b'A'*32/b'B'*32，不需改共享夹具。
必须补真实双key绑定判据：现newtest仅assert两MAC不同不够（不同evaluation_id本就不同）。从真实PG approvals行构造ApprovalBinding（参考w4b1/test_binding.py或该service里verify_binding签名），分别用自己key验证成功、另一app key失败；确认global_config置None或错误key/B0不影响实例创建。利用monkeypatch.setattr恢复全局，不能测试结束遗留污染其他库测试。
实例配置校验不要len任意对象/直接调用任意wall_now：bytes且>=32、profile有效、wall_now callable+aware；不完整配置统一GatewayError DEPENDENCY_UNAVAILABLE503，不ValueError500，不回退global。生产两处通过getattr(state,'approval_hmac_key',None)等，丢key时明确503；测试缺key/短key即使global有效也不能成功审批。不改消息HMAC/TTL/锁序/状态机。
先写进度骨架，修完立即跑newtest、make test-w3b test-w4b1 test-w4b2b test-w4c（每个alarm300，-q末尾汇总），ruff check --fix/format限定文件，不跑大范围。先红旧调用不传instance：newtest必须503失败；恢复绿。
预算最后5轮无论如何本地提交检查点并给真实命令结果，别堆WIP；禁live/env/secret/长期栈up/down/reset/删卷/push。完成回复SHA/结果/未覆盖。
