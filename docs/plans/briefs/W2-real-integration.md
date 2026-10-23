# W2 真实W1来源链与S3预留接线（协调任务，2026-10-24）

沿用W2-provenance、wave1-common、PRD7.3–7.6与9.3/9.5。先W1 PR13合入main，同步后才启动W2a；S3a通过完整门禁合入后才启动W2b。实现/独审均本机DeepSeek CLI；稳健优先，两个子组件串行，不提前开放缺依赖路径。

## W2a：真实身份/原话接入与memo P2

输入：真实W1 G1/G15/G3/G19/G20/load_for_plan及全局撤销，W2既有read_records和阶段②–④。输出：同一真实gateway PG中的来源链验证，G2生产鉴权不再依赖fixture_revocation替身；裸¥/￥且邻近没有数字不升级，真实金额继续黄级。

- 允许原W2文件范围、tests/gateway/w2、docs/progress/W2-provenance.md；只做test-w2目标必要末尾追加。冻结W1/auth/state/runtime/main/迁移/contracts/bankcore/L3/依赖/AGENTS/PRD。发现不兼容先报告，不自己改另一owner。
- 先保留并提交未提交memo只读评审报告，协调者同步main（合并如有业务冲突由本worker在明确范围处理）；不覆盖旧检查点8063f27。
- 删除G2 DB测试中fixture_revocation的替身，使用真实runtime、PIN材料、app+assistant JWT与数据库会话。单元级隔离端口可保留，但不能继续用fixture假装集成成功。
- 新建真实G3→load_for_plan→②③④贯穿：typed正常金额/目标；同名澄清G19/G3 action合槽；hint实际采纳used_by；原typed parent span追溯；当前root唯一scope claim；跨sid/customer、未采纳hint、错误used_by、失效parent、改口后旧utts、logout后JWT全部拒绝。
- G2读取真实HTTP路由+read_records持久化、注销前后相同请求的401/409语义，真实check_revocation，不抹平鉴权失败。L3未有的工具用契约FakeL3且显式登记，不能算真实银行HTTP。
- memo P2先红：单独¥/￥、普通“价格符号¥”、前后无数字都不产生MEMO_SENSITIVE_CONTENT；¥1/￥1、1元、8位数字仍升黄；来源DENY/UNKNOWN不被黄finding覆盖。不得放宽来源白名单或吞掉真实授权错误。
- 新测试必须先实际红测再实现；退出make lint/test-gateway/test-gateway-db/test/test-w2 + actionlint，W2专项零skip/xfail。新增真实W1联测不能靠跨目录pytest fixture隐式替身。
- 冻结提交后停止，协调者完整复跑与DeepSeek独审；W2b是后续组件，未完成不能把W2整体发布为完成。

## W2b：default reserve真实生产接线

须S3a合并后重新同步main；输入为真实BankState.obligations完整快照，输出为ProvenanceStage默认直接使用唯一bankcore.projected_shortfall，保持days=7；显式测试注入可用于纯单测，但生产不能默认None，也不能从旧View或余额猜零预留。

- 不重复业务算术，不改已冻结签名。None/coverage不完整明确依赖错误，不把失败当0；负max_outflow保持有符号语义，不clamp。
- 真实独立PG/L3 snapshot HTTP→W2 derived模板available_less_7d_charges验收295500/295501与版本变化，声明为W2来源组件集成，非W3完整Decision或W4执行通过。
- cancelled_monthly_fees用真实已存月度授权和同计划取消集合；WEEKLY仍按PRD拒绝，模板动作绑定不放宽。尚未实现取消领域写则只验证来源和范围，登记对应业务未联调。
- 原五退出+actionlint+实际PG/HTTP集成零skip，评审通过后完整W2 PR；如需跨层新fixture目录/Makefile/CI只能在协调者明确批准后追加，不能绕main单独复制另一分支生产模块。

## worker公共要求

单条shell命令不用 ; 或 &&。本地提交不push，提交末尾Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>。进度写docs/progress/W2-provenance.md，最终简短。禁up/down/test-p1*/test-integration/docker volume/prune/reset/TRUNCATE现有业务库。make demo当前未实现须明确报告不可用，不能伪写replay通过。实际完整演示待C1与W4后另验。
