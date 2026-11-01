# PRD第二轮复测修复计划

> 执行：多Codex子agent独立worktree；根代理串行集成、真实PG、完整CI与发布。按用户已授权方式直接实施，不再讨论架构。

**Goal:** 修复8e2810e复测报告中的候选继续、建议按钮、查询卡片与预留展示缺陷。

**PRD归属补充：** 同时参考`docs/reviews/prd-retest-report-2026-11-01/alignment/remaining-issues.md`与`requirement-provenance.md`。R1/R2是原定稿明确功能；R3展示修复、R4体验改进、R5接口语义、R6文档冲突分别记录，不能把历史8/14/2当PRD功能通过率。

**Spec:** `docs/reviews/prd-retest-report-2026-11-01/REPORT.md`、`PRD.md`现有G3/G4/G19/G22a/F4/UICard协议。

**Architecture:** 保留三层及确定性授权。修正边界数据适配与展示投影，不把action/hint改写为typed、不自动采用建议、不清除模型上下文污点。

**Global constraints:** 仅自动开发验收；无生产模型、浏览器演示、reset、录制、tag或公开进度。旧结果/WIP/分支/卷保留；根代理独占实际PG和唯一整栈；资金与安全改动独立只读评审。最终完整40位head全部本机CI PASSED才按head合并。

**Review focus:** 合法action/hint空文本；typed空值拒绝及G20权威scope；真实hint.id和G22a label；带恶意文本的只读显示；未计算预留与真实0的区分及B1兼容。

## Task 1：候选继续入口（Agent A）

- 文件：`services/agent/app/main.py`及已有gateway adapter；新HTTP/SSE编译图测试。
- 输入：G3合法action/hint `text:null`；输出：进入真实ingest、G20恢复权威来源，继续正常规划。
- [ ] 真实HTTP入口旧失败测试，保留typed拒绝/过期和错scope负控。
- [ ] 复用已有nullable适配，专项绿；独立评审。

## Task 2：持久建议与只读卡投影（Agent B，两个单元串行）

- 文件：`services/agent/app/llm/cards.py`及真实wire形状测试。
- 输入：GatewayDecision生产`replan_hints[].id`；输出：`CardAction(kind=HINT,ref=id)`，标签继续由G22a验证。
- [ ] 真实wire旧红→新绿，不以旧ref假夹具替代；独审。
- [ ] 读取卡按已知污点字段显示安全纯文本名称，保留原始ReadRecord及来源；只对已证明定位用途的中间读取抑制卡片，真实持仓名必须来自成功读取；旧红→新绿，独审。

## Task 3：客户可读展示（Agent C）

- 文件：`frontend/src/mobile/Assistant.tsx`、必要renderer与测试。
- 输入：真实UICard；输出：名称/整数金额/上海日期、可读表格，无协议包装和技术ID。
- [ ] 生产形状旧红→新绿；安全文本/Markdown不执行HTML，不改授权与标签门禁。
- [ ] 与B集成，完整前端/BFF/build及交叉评审。

## Task 4：预留语义与文档（root）

- 文件：gateway preflight/context/decision/schema、Agent wire、FE Preflight/Approval、PRD。
- 保留`scheduled_reserve_cents`为计划前七日义务；增加显式计算状态。非转出操作最佳努力显示真实计划前值，不引入资金拒绝规则；缺失投影显示未计算，现有转出依赖失败仍503。
- [ ] 取消/赎回真实4500、真实0与未知及B1红测；实现并跑受影响自动检查，独审。
- [ ] PRD §2.2旧三异常文字与既有canonical九月4/全窗5对齐，不改种子和旧报告。

## Task 5：集成与发布

- [ ] root核对全部独审，实际PG通过、BFF→HTTP/建议→卡片集成自动覆盖；逐项更新AGENTS §3/4及acceptance。
- [ ] 停止保留另一演示栈后串行唯一CI栈；每次fresh P1前无损重命名当前测试库。
- [ ] 精确完整head全部本机CI PASSED，按既有授权普通push/PR/匹配head merge commit。
- [ ] 运行栈恢复合并main，验证源码/容器健康和只读账本；交用户在其他对话复演。不把自动测试当现场24或43项全通过。
