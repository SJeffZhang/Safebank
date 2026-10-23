# S1f：G22 按演示开关实际挂载

2026-10-23，共享运行接线精确补缺。PRD9.5 G22与W1任务要求仅DEMO/EVAL注册；目前auth.router总挂载且handler仅404，coverage_errors还总要求该路由。W1 owner已确认待决仍开放，不越界。此组件在S1d后、S3a实现前串行完成，独立feat/l2-demo-route-guard，复用已完成共享runtime worktree从最新main。

输入Settings.demo_mode/eval_mode；输出每个public app的真实路由集合：两者都False时无GET /auth/demo-customers，任一True时存在且凭证要求不变。health/其他auth/public/internal/demo原规则保留。不能只靠handler404，不改W1正在修改的auth.py，不改MATRIX任何key/凭证/授权组合或既有函数签名。

允许main.py公共router装配、auth/matrix.py仅coverage_errors的条件挂载校验、相关shared_runtime/现有test_matrix测试、README、进度/验收文档。auth.router可重用，不能修改全局router.routes或共享缓存导致先建off app后建on app缺路由；须采用每app独立的安全装配，兼容当前FastAPI嵌套/lazy router。覆盖校验需在两开关关闭时允许缺失并拒绝误挂此演示端点；开启时要求存在。MATRIX权限字典/lookup/match不变，内部app不要求公共G22。不得改config结构、冻结schema/state/contracts/迁移或其他owner业务。

真实行为先红后绿：off/off路由集合缺失且HTTP404；on/off、off/on、on/on路由存在且仍F_SERVICE；先on再off再on与先off再on独立构建不污染全局router；其他auth/health/内部路由与原全矩阵测试保持；coverage missing/extra两方向及错误装配failclosed。main尚未W1时可用既有stub证明挂载/鉴权边界，不能称W1客户信息业务验收。

完整退出make lint/test/test-gateway/test-gateway-db/test-shared-runtime/contracts-check；PG/secret专项实际开启零skip。协调者亲自同六退出与actionlint、独立只读PRD审核，修完立即push/base main PR，精确head四CI成功后merge commit。无新workflow目标/新依赖/新表，不动Docker数据卷。只本地提交冻结，报告docs/progress/S1f-demo-route-mount.md准确红绿/失败/未覆盖；不push/PR/merge、派agent或up/down/test-p1。
