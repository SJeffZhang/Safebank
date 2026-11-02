# SafeBank 第二轮复测修复：独立最终需求与整合审查

- 日期：2026-11-01
- 审查者：Codex 独立只读需求审查子 agent；未参与本轮实现。
- Base：`8e2810e6764e3fd3e37406b31e694605751e0356`
- 固定最终审查 SHA：`c8a881d5af6f46904a5df5367ef91b60c363e2bf`
- 结论：**APPROVE**。在本次 R1–R6 与六场景受影响边界中，未发现需修复的 P0/P1/P2。
- 本结论是代码、接口说明与需求归属审查；不替代 root 对最终完整 40 位 head 的本机完整 CI、PR 合并或后续现场复验门禁。

## 依据与范围

读取 AGENTS §2/§3/§8、PRD §5/§7.4/§7.7/§7.9/§9.2/§9.5/§9.10，原始复测 `docs/reviews/prd-retest-report-2026-11-01/REPORT.md`，及 `alignment/remaining-issues.md`、`alignment/requirement-provenance.md`。审查 base 至最终 SHA 的生产、契约镜像、测试与 PRD diff；额外对照实际 ReadEnvelope、CardView、ProductView、FundOrderView 字段以及 G20 ingest、G22a/G3 使用链。

未运行 Docker、数据库、部署、生产模型、浏览器或 demo；未修改生产代码、共享 AGENTS/PRD 或他人 WIP。报告仅写入 `/private/tmp`。

## R1–R6 核对

| 项目与需求归属 | 独立审查结论 |
|---|---|
| R1：原有 T-1/X-3 候选继续路径 | HTTP 与 G20 共用 `utterance_ref_from_fields`，只将合法 hint/action 的 null 表示为本地空字符串；typed null/非字符串、未知 kind 仍拒绝。Gateway graph 仍按 ID 调 G20，并以服务端内容替换本地 text/kind/scope/parse_status/parent/hints。未创建 typed 根、放宽 G19/G3 候选或授权约束。 |
| R2：原有建议入口 | 按真实 GatewayReplanHint.id 生成仅含 kind/ref 的 HINT action，不再读取虚构 ref 别名。前端按钮标签仍从 G22a 读取，用户点击仍经新 G3 与新 G4；既有单次消费、parent/digest、TTL、epoch、审批、OTP 验证未改。root 新 PG 测试接入实际 G4 wire→卡片 ref→G22a→G3→新预演，但 L3 快照替身的边界记录准确。 |
| R3：展示实现改进 | `_read_card_data` 构建 dict/list 副本，只有声明的 JSON pointer 和相同 source 的精确外层包装才去一层；原 ReadRecord、untrusted_paths、source_ref 与 prompt 数据保持不变。显示字符串不产生 CardAction。只有完整、精确对应的实际比较/商户筛选结果才隐藏定位扫描页，缺失、错窗口、错目标、歧义仍保留。 |
| R4：展示体验改进 | 前端新增展示组件全部经 React 文本节点输出，安全表格/加粗不解析 HTML、URL、图片或写动作。未知产品名采用待核占位，不内置 ID→产品名推断。实际同轮成功产品读取按真实 product_id 一致关联时，附 product_name_source_ref，PRD 已明确其额外来源，未冒称持仓数据源。带时区时间换算上海，交易日/净值日保留有效日历日期；非法日期不归一化成另一天。 |
| R5：已有字段语义澄清 | scheduled_reserve_cents 明确为计划前七日总义务，reserve_projection_available 区分计算所得 0 与默认未计算 0；前端旧记录/false 显示未计算，true 才显示计划前值。纯取消、赎回、冻结等最佳努力计算只供展示，不新增保护动作拒绝规则。既有转出 `_reserve` 的依赖缺失仍由 `_project` 503 失败关闭；B1 继续跳过累计投影并标 false。未改 L3 资金或审批/OTP。 |
| R6：后续文档冲突修正 | §2.2 明确原三个预埋场景与后续 §8.2 四/五条 canonical 输出的区别；diff 不修改种子、生成器、异常算法、阈值，也不隐藏多出的既有信号。四/五条没有被描述成 10 月 23 日原始要求，历史现场和 CSV 均保留。 |

仅 R1/R2 属原定稿明确功能路径修复。R3/R4 展示、R5 字段语义、R6 文档一致性保持分别归属，不混算银行功能失败或成功；未将 24 项历史 8 PASS/14 PARTIAL/2 FAIL 重算成 PRD 功能通过率。

## 六场景 29 项受影响边界

| 场景与需求 ID | 已审查边界与保持项 |
|---|---|
| 转账 T-1–T-5 | 候选 action 经权威 G20、hint 需用户选择与重新预演；展示名称不产生新增收款人权限或转账动作。预留 503、审批/OTP 保持。定时、AA 业务实现本轮未变。 |
| 账单 B-1–B-4 | 商户筛选结果与相同窗口相符后才抑制扫描卡；原数字、来源、月份不匹配防线保留。异常数量只统一文档，不改检测输出。 |
| 卡片 C-1–C-6 | 卡/账户只读内容与保护动作预留展示；限额、渠道、挂失、冻结风险与写路径未变，缺展示投影不新增保护动作拒绝。 |
| 订阅 S-1–S-5 | 名称、日期、商户/定时预留分列；取消面板为计划前义务，不能当作取消后义务或实际冻结资金。取消授权、到期扣费与 grant 撤销路径未变。 |
| 理财 W-1–W-5 | 选定产品 null action 可入图、名称须真实读取关联、未知名称保守占位、份额保留 Decimal 字符串、在途订单与可用资金分列。问卷、适当性、两阶段、资金/赎回算法未变。 |
| 联动 X-1–X-4 | X3 产品选定仍由 G20 及 L2 派生模板约束；X1 读取展示与月份校验保留。累计预演、保护动作、未到账资金不可花用和原子执行路径未被显示投影替换。 |

该表是受影响边界审查，不是全部 29 项新现场验收。

## 独立命令与实际结果

1. Python 专项，执行时 SHA `c0830b26f36578196eaa9b3d9d54cbb6973d9598`：

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/agent/test_cards.py tests/agent/test_retest_nullable_entry.py tests/agent/test_retest_hint_cards.py tests/agent/test_retest_read_cards.py tests/gateway/w3a/test_retest_reserve_display.py tests/agent/gateway/test_wire.py
101 passed in 1.17s
```

真实 FastAPI/JWT、编译图与适配器可在此专项中运行，但所需外部 HTTP/provider 使用脚本替身、checkpoint/锁使用内存替身；不代表真实 L2 数据库授权或 L3 资金执行。

2. 集成后前端，执行时 SHA `269e53f624c8bf726745493b450079598f394665`，在 frontend 目录：

```text
npm test -- --run src/mobile/AssistantReadPresentation.test.tsx src/mobile/AssistantWire.test.tsx src/mobile/AssistantComparison.test.tsx src/mobile/AssistantClarification.test.tsx src/mobile/AssistantTopics.test.tsx src/mobile/Approval.test.tsx src/mobile/Approval.material.test.tsx
7 files passed; 76 tests passed; duration 4.36s
```

为 jsdom/模拟网络单元，涵盖安全文本/表格、日期、未知名称、金额未知值、来源不变、澄清与月份防线以及既有审批/OTP；不代表浏览器真实服务链已复验。

3. 固定最终 SHA 后验证已测试代码不变：

```text
git diff --exit-code c0830b26f36578196eaa9b3d9d54cbb6973d9598..c8a881d5af6f46904a5df5367ef91b60c363e2bf -- services tests
# exit 0; no Python service/test differences

git diff --exit-code 269e53f624c8bf726745493b450079598f394665..c8a881d5af6f46904a5df5367ef91b60c363e2bf -- frontend
# exit 0; no frontend differences

git diff --check 8e2810e6764e3fd3e37406b31e694605751e0356..c8a881d5af6f46904a5df5367ef91b60c363e2bf -- services frontend tests PRD.md AGENTS.md docs/acceptance
# exit 0
```

最终新增的 PRD nullable/display 来源解释已独立核对与实现一致。全仓 base..head diff-check 另会对原样导入的 results.csv CRLF 与 initial-prd-excerpts.txt 尾空白报警；此为保留原证据字节的归档格式，不是产品发现，未擅自规范化档案。

## 未验证项与发布门禁

本审查未重跑真实 02/38/40 浏览器动作、原 43 句、生产 DeepSeek、真实银行资金/账本、到期事件或重投恢复，未进行两轮 reset、生产录制、断网或冻结验收；这些缺口不是已发现故障，也不能表述为通过。root 的真实 PG/完整 CI 回执只由 root 记录，不能用本报告或作者测试替代最终精确 head 完整本机 CI 合并门禁。后续若有生产代码变动，本结论需针对新增 diff 复核。
