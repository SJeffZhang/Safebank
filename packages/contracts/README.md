# contracts

三层共享的 Pydantic v2 契约：18 个读工具、14 个写工具的参数与结果、PlanDraft、不可变 BankState、LedgerEffect 和执行/恢复协议。金融金额拒绝 bool/float/数字字符串；Decimal 以四位小数字符串序列化；时间必须显式带时区；额外字段拒绝。

LLM 使用 `schema.plan_tool()` 生成的 13 工具 strict Schema，排除风险问卷和 App action 来源。`to_strict_plan` / `from_strict_plan` 转换实体引用、空值和渠道哨兵；工具 Schema 不替代运行时校验或 L2 授权。

根目录 `make setup` 安装精确依赖；`.venv/bin/pytest tests/contracts -q` 独立验收。无需环境变量。DeepSeek 实测证据在 `docs/acceptance/p1-contracts-live/`；实测脚本需要本地 `.env` 的 DEEPSEEK_API_KEY，会实际收费。

冻结快照为 `docs/contracts/v1.json` + SHA256；`make contracts-check` 检查漂移。修改契约必须同步调用层、PRD 和版本决策，再更新快照，不得只重生成文件隐藏兼容性变化。
