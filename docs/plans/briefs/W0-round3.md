# 任务书 W0 第三轮：修完复审阻塞项

第二轮复审仍未通过，见 `docs/reviews/L2-W0-architecture.md` 末尾「第二轮复审」章节（完整阅读）。本轮只修复审列出的问题，不扩展范围。**最高原则：稳健与架构合理优先于速度。**

## 1. 必须修（阻塞项）

1. **P0-3 工具注册表**：新建 `services/gateway/policies/tools.yaml` 与 `services/gateway/app/policy/registry.py`：`ToolPolicy` 数据结构（ActionKind、基础等级、敏感参数、allowed_origins、outflow、标题模板、是否不可逆、是否需 OTP 等 PRD §9.4 / §7.6 / §9.5 PlanView 需要的字段）；`action_kind(tool, args, shadow_state)`（卡类工具按新旧值判定 TIGHTEN / LOOSEN，PRD §5.3、§7.6）；加载函数并校验工具集合恰好等于 14 个写工具；按 PRD §9.4 填好全部 14 个工具的值。README 写明：此后 W2 只能改值、不能改结构。
2. **N1 公共路由鉴权**：public_app 全部路由接入 `caller_dependency`（并正确传入公钥）；不带凭证调用任何非 `/health` 公共路由返回 401；按矩阵默认拒绝。
3. **N2 状态迁移**：`transition` 必须执行合法迁移表，非法迁移抛错并有测试；补齐复演与重新审批需要的状态边（如 CONFIRMED → PENDING_APPROVAL / DENIED / REPLAN，以及 PRD §7.10 中 STATE_CHANGED、SUPERSEDED、取消词导致的迁移）。
4. **N2b 交叉接口占位落成代码**：`approval/service.py`、`approval/planview.py`、`evaluation/repo.py`、`locks.py` 与执行前复演入口，按评审建议写出签名、输入输出模型、`NotImplementedError` 占位、docstring 标明实现方（W3 / W4），并在 README 分工表登记。

## 2. 必须修（P1）

5. **N3** `bank_state()`：修正 dict / 模型处理；取快照时传入计划步骤，使收款人目录与对方客户数据能被加载。
6. **N4** 审计哈希前统一把时间转成 UTC；在临时 Postgres 上用 +08:00 时钟验证不误报。
7. **N5** `request_dedup`：`produce` 抛异常时清理进行中记录（或标记失败可重试）；进行中返回 409 `EXECUTION_IN_PROGRESS`。
8. **N6** 阶段结果类型移到各拥有者模块，或在 README 明确「拥有者可以往自己的结果类型加字段」；`EvalContext` 增加 `steps` 字段，由 ② 写入。
9. 复审标为「部分修复」的 P1-13（时区）一并收尾；P1-8 遗留的 P2 小问题能顺手修就修，否则写进进度文件。

## 3. 探针转测试

评审用于复现问题的探针 A–D 全部写成正式测试，放在 `tests/gateway/`（需要数据库的放 `tests/gateway/db/`），修复后必须通过：A 无凭证调公共路由得 401；B `bank_state()` 正常返回；C +08:00 时钟下审计链校验不误报；D 评审报告中第四个探针所验证的行为。

## 4. 约束

- 可改范围同第二轮任务书。
- **禁止删除、重建或 `docker volume rm` 任何 Docker 数据卷**，也不要用 `docker compose down -v`。需要全新数据库时只用 `make test-gateway-db` 的临时容器。
- 不 push；不改 `packages/contracts/**`、`packages/bankcore/**`、`services/bank-backend/**`、`AGENTS.md`、`PRD.md`。

## 5. 退出条件（全部实际跑过）

`make lint`、`make test-gateway`、`make test-gateway-db`、`make test` 全部通过；`make up` 全部服务 healthy（在现有卷上，不重建卷）后 `make test-p1` 通过。

## 6. 交付

本地提交（提交信息末尾 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`），更新 `docs/progress/W0-foundation.md`（逐条列出本轮处理与证据），最终回复简短：完成项、退出命令结果、未完成项、待决。
