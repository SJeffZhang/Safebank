# 读取卡片展示投影：R3 / R4

范围：协调者追加的 cards.py 串行单元。R3 是展示实现问题，R4 为体验建议；依据 2026-11-01 的 remaining-issues 与 requirement-provenance 报告，不将本修复计入银行功能或 PRD 通过率，不改写原 43 句记录。

## 输入与输出

输入：同轮成功 ReadRecord（原始 data 保留上下文包装，含 source_ref、untrusted_paths）。输出：可读 UICard 展示副本。name / merchant_name / display_name / counterparty / target_display 只保留普通字符串；按 JSON pointer 和同一 source_ref 精确去掉一层 outer wrapper，异源、未标记或嵌入的片段原样保留。敏感 memo/alias/地址等原过滤保持。

原 ReadRecord、污点路径、source_ref、模型提示和写动作来源不变。展示字符串不生成 action；React 是否按文本节点渲染由前端单元验证，本单元不宣称浏览器 XSS 实验通过。

仅当完整且确切对应的实际 compare_products 或带 merchant_id 的 filtered_all get_transactions 结果存在，才隐藏用于定位的候选 / 扫描页，避免 5 卡上限挤掉最终结果。未完整、错窗口、错目标、歧义时保留原读取，不推断无数据。正常产品列表查询不因此隐藏。

HOLDINGS 只按真实 product_id 关联同轮成功 list_products/get_product/compare_products 的 name，追加 product_name 与 product_name_source_ref；未知 ID 或冲突名称不猜。持仓主 source_ref 仍保留。

## 实测

- 原码新增回归：`pytest tests/agent/test_retest_read_cards.py -q -p no:cacheprovider`：10 failed、10 passed。失败覆盖删名称、原包装上屏、扫描/候选挤掉最终结果、缺少实际产品名称关联；负控先绿。
- 修复后专项（cards + hint cards + read cards）：55 passed，含真实 GatherAdapter 包装和完整 compiled read graph 的 merchant/compare 两种路径。
- 受影响 Agent 无数据库全套：`.venv/bin/pytest tests/agent -m 'not db' -q -p no:cacheprovider`：1107 passed、42 deselected（与 hint 单元套件重叠，不相加）。仅既有 localhost transport 测试需沙箱外监听本机；未访问真实服务。
- `ruff check --no-cache`、`ruff format --check`、`git diff --check` 通过。

测试证明未修改原始 read、名称作为字符串不生成任何 HINT/CLARIFY/write action，业务金额和 source_ref 保留；JSON pointer 转义、错 source、未标记 wrapper、hostile HTML 字符串、分页/缺失/歧义/不匹配与名称冲突均有负控。

未运行：模型、真实 Gateway/L3 API、数据库、Docker、浏览器、演示、部署、完整 CI。前端 React 纯文本/交互验证及最终精确 head 门禁由协调者负责。共享 AGENTS/PRD 未改。
