# W2b：真实完整义务快照与默认预留接线（2026-10-24）

前置：S3a #15、W2a #16 已合 main eea02a6。沿用W2-real-integration.md、wave1-common、PRD7.5/7.6/9.3/9.5、ADR-009。实现/独审用原DeepSeek CLI；只做本组件，不实施W3/W4/义务业务写，前端/BFF暂停。

输入为真实L3 snapshot HTTP取得的BankState.obligations完整数据，输出为阶段③派生模板对唯一bankcore.projected_shortfall(days=7)结果的确定性核对。默认生产ProvenanceStage直接使用该纯函数；显式测试依赖注入保留，但生产不默认None，不复制算术，不从旧余额/View猜零预留。

允许 services/gateway/app/pipeline/provenance.py、确需的W2 taint/intent既有所有权文件（如需改说明证据，不扩权）、tests/gateway/w2/**、docs/progress/W2-provenance.md。协调者额外明确批准：Makefile test-w2命令只增加 OBLIGATIONS_DB_TESTS=1；.github/workflows/checks.yml p1-postgres在test-obligations后追加make test-w2，使真实W2 PG专项在CI实际执行，job名不改。其余共享接口/运行层、contracts/bankcore/L3/迁移/依赖/PRD/AGENTS/其他任务测试冻结。

- None/coverage不完整或不一致/无目标账户必须明确fail-closed依赖错误，不变成0、unknown成功或裸KeyError；仅在证据成立且模板需要时求预留，已有hard DENY不被后续503遮盖。不得用宽except吞编程/连接错误。负max_outflow保留符号，禁止clamp。测试显式reserve=None是否保留由最小清晰API决定，不能为旧测试硬撑错误默认。
- available_less_7d_charges绑定正确动作/账户与已登记read来源；真实独立PG→实际L3 snapshot HTTP→ProvenanceStage默认实例→③结果贯通，295500匹配、295501不匹配、版本/模拟时刻变化使旧来源拒绝；必须调用真实bankcore，不把fake reserve当生产证据。snapshot/read_records两端一致性由现有机制验证，不抹平来源校验。
- cancelled_monthly_fees使用真实持久化月度mandate/订阅视图与同计划cancel_subscription集合，校验去重、账户、ACTIVE/MONTHLY和目标本人储蓄；WEEKLY/非月度/跨账户/重复/缺失来源拒绝，不能把未实现取消领域写说成执行通过。
- 复用S3a测试fixture/真实loader与PG16临时容器，不复制生产模块或往长期cus_a/b种子装义务。允许测试内导入既有tests.obligations_data.conftest/fixtures辅助；避免app包导入冲突、隐式fixture monkeypatch。临时测试schema重置只允许新建tmpfs独立测试库；禁止长期业务库reset/TRUNCATE/删卷。
- 先新增默认实例/真实S3接线的失败测试并实际红测，再最小实现转绿。专项所有新增真实HTTP/PG用例在make test-w2实际运行，零skip/xfail。保留W2a真实W1与memo234项，不移除安全断言。

退出make lint/test-gateway/test-gateway-db/test/test-w2（零skip/xfail）、make test-obligations（零skip）、contracts-check；actionlint协调者另跑。make demo未实现要明确，不能伪称完整W3 Decision、W4执行、demo/replay联调。进度记录真实命令/边界；本地提交末尾Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>，冻结停止。单条shell命令不用 ;/&&；不push/PR/merge/派agent/up/down/test-p1*/test-integration/docker volume/prune，不读打印任何密钥。
