# S1d memo 敏感内容黄级代码

2026-10-23；分支 `feat/contracts-memo-code`；输入基线 `a1762931689a9b3e879d17ddc267da3743416c74`（包含已合并 S1e）。本组件只本地提交，发布由协调者办理。

## 输入、输出与范围

输入为既有 FindingView / Violation / Decision wire 数据中的 `MEMO_SENSITIVE_CONTENT`。输出为共享 Code 枚举识别该值、现有模型按原结构解析及 JSON 往返；FindingView 示例效果为 `RISK_YELLOW`，Decision 示例风险为 `YELLOW`，Violation 示例为 `hard=False`。未知 code 继续拒绝。测试同时以现有 JSON Schema 校验往返结果。

唯一生产代码变化为 `Code.MEMO_SENSITIVE_CONTENT`；PRD §7.5 与 §9.8 登记原有 ≥8 位数字或金额格式升黄规则，未改变阈值。契约版本继续为 1.0.0，本次受控兼容增量仅增加枚举成员；更新 v1.json 与 SHA256，不改字段、工具签名、registry、迁移、依赖或授权行为。旧消费者须先同步新枚举才能识别新值。

检查现有 `services/gateway/app/schemas/decision.py`、共享 `plan.py` 和 policy registry：FindingView / Violation 直接引用 Code，Decision 包含 FindingView；无固定 code→风险映射阻止新值，无须修改 registry 结构。模型本身不会自动把新 code 转成 YELLOW；后续 W3 决策必须保留 W2 产生的 RISK_YELLOW finding。

## 实际验证

初次普通沙箱写附加 worktree 被文件系统拒绝，测试文件未创建，pytest exit 4；随后受控提升写入成功。该环境失败不算红测。

1. 先新增测试，未修改枚举：`.venv/bin/pytest tests/contracts/test_memo_code.py -q` → **3 failed / 3 passed**，exit 1，0.10s。三个失败均为新 code 不在 Code 枚举；未知 code 三模型均拒绝。
2. 新增枚举与 PRD 登记后同命令 → **6 passed**，exit 0，0.08s。
3. 更新 freeze 前 `make contracts-check` → **预期失败**，exit 2，提示 `Contract changed: v1.json`。
4. `PYTHONPATH=packages/contracts/src .venv/bin/python -m scripts.freeze_contracts` → `Generated contracts v1 candidate`。
5. 递归比较基线 `git show a1762931689a9b3e879d17ddc267da3743416c74:docs/contracts/v1.json` 与当前 JSON，要求键集合一致、所有非 Code enum 值相等，Code enum 仅在 INJECTION_SUSPECTED 后插入 MEMO_SENSITIVE_CONTENT；实际仅两处：`/models/ExecutionResult/$defs/Code/enum` 与 `/models/ExecutionStatus/$defs/Code/enum`。另外精确比较 read_tools / write_tools / llm_tool 全部相等 → **PASS**。
6. 完整退出实际顺序执行：`make lint` → **PASS**（175 files already formatted）；`make test` → **398 passed / 302 skipped**，exit 0，4.45s；`make contracts-check` → **PASS**，exit 0。

新增 6 项专项均非 skip / xfail。全量跳过项包含需显式环境开启的真实 PG / Linux 专项；本组件不操作服务或数据库，未将这些跳过项计为真实集成验收。未运行 up/down/test-p1，未操作 Docker 数据卷。

## 后续业务边界

此处仅验收共享代码可序列化，不等于 W2–W4 风险业务验收：

- W2 owner 同步后落实既有 TODO、删除严格 xfail，独立测试普通文本不升黄、7/8 位边界、金额格式、memo 与 alias、来源限制不放松。
- W3 完整决策需验证新 finding 保留 YELLOW，不能降为 GREEN 或冒充注入 RED / 污点 DENY。
- W4 完整 PlanView 需验证非空 memo/alias 原文展示并绑定 view_hash，仍待真实跨组件联测。

本次没有实现上述业务，没有修改 W2/W3/W4 owner 文件。AGENTS 总状态由协调者统一维护。独立只读评审、协调者亲自复跑、push/PR/远端全绿 CI/merge 均待协调者完成。
