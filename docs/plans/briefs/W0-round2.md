# 任务书 W0 第二轮：按架构评审修订骨架

W0 第一轮（378ba67）没有通过架构评审。评审报告：`docs/reviews/L2-W0-architecture.md`（请完整阅读）。**最高原则不变：稳健与架构合理优先于速度。** 本轮目标是让骨架成为 W1–W4 并行开发的稳定基础：开工后任何两个任务都不需要改同一个文件，也不需要各自发明共享数据格式。

## 1. 必须完成

- 报告中的 **P0-1 至 P0-7 全部**、**P1-8 至 P1-14 全部**，按报告给出的修改建议实现（与下面「指挥决定」冲突时以本节为准）。
- P2 只做「改动很小且能减少后续冲突」的条目，其余写进进度文件留给后续任务。

## 2. 指挥决定（照此执行，不再讨论）

1. **P0-5 在 W0 里做接口占位**：为 plans / approvals / utterances / evaluations 的状态迁移建立**唯一入口模块**（状态迁移函数 + 合法迁移表 + 测试），各任务只能通过它改状态；G4 需要的「创建审批 / 计算 view_hash」、W4 需要的「执行前复演」各提供一个**接口占位**（签名、输入输出模型、`NotImplemented`），分别标明由 W4、W3 实现。目标：W3 与 W4 可以同时开工。
2. **迁移规则**：W0 本轮把报告指出缺失的表、字段、约束一次补进 `0001`（W0 尚未合入，可以直接改 0001）；同时加「冻结 DDL 与 SQLAlchemy 表定义一致」的防漂移测试。波次 1 期间 worker **不写迁移**，需要改表写进待决，由指挥在集成分支串行追加 `0002_wave1`。在 `services/gateway/README.md` 写明这条规则。
3. **阶段归属**（写进 README 的分工表）：
   - ① contract、② entity、③ provenance、④ intent → **W2**；
   - ⑤ snapshot、⑥ preflight、⑦ workflow、⑧ decision、⑨ audit 阶段、G4 组装 → **W3**；
   - W1 负责原话 / scope 的生产，W4 负责审批与执行。
4. **② 实体阶段的数据来源**：在 `EvalContext` 上提供一个**惰性、带缓存的 L3 状态访问器**（首次调用时取 `/internal/snapshot`，之后复用同一份）；② 用它核对所有权，⑤ 只负责钉住 versions / clock_version 并补足预演所需数据，不重复拉取。这样不改变 PRD §7.2 的阶段顺序。
5. **状态枚举**：`ExecutionJobStatus` = EXECUTING / UNKNOWN / EXECUTED / FAILED / CANCELLED；`WorkflowEventStatus` 采用报告的建议值。两者都加 CHECK，并在进度文件中列出，由指挥补进 PRD §7.2。
6. **P1-12 允许改 `scripts/`**：可新增 gateway 库引导脚本（仿 `scripts/p1_database.py`：库 owner 与运行角色分离、创建 `gateway_admin`、运行角色对 `audit_events` 只有 INSERT / SELECT、默认权限覆盖以后新建的表），并把 gateway 迁移接进 `make up`。必须保证 P1 的 `make up`、`make test-p1` 不被破坏。
7. **P0-7**：在本分支补齐运行镜像依赖（`requirements*.txt`、`.wheels` 生成流程按仓库现有做法），确保 gateway 容器能启动。

## 3. 可改范围

第一轮的范围，加上：`scripts/`（只新增 gateway 引导相关脚本，或在现有脚本中做最小必要改动）、`requirements*.txt`、`docker-compose.yml` 中 gateway 与 postgres-bank 初始化相关的部分、`Makefile`（up 流程中接入 gateway 迁移）。仍然**不改** `packages/contracts/**`、`packages/bankcore/**`、`services/bank-backend/**`、`AGENTS.md`、`PRD.md`。把评审报告 `docs/reviews/L2-W0-architecture.md` 一并提交。

## 4. 退出条件（全部实际跑过）

- 第一轮全部退出命令，外加：
  - 授权矩阵覆盖检查：所有已注册路由都在矩阵中，未登记路由默认拒绝；带参数路径能匹配；一条路由可登记多种凭证组合。
  - 框架 404 / 405 / 未捕获 500 都返回统一错误体。
  - JWT：数组 aud、错误类型的 scope / utts 被拒绝；app 与 assistant 令牌互不通用。
  - fake L3：真实超时、丢包、「已提交但回包丢失」、令牌校验、可自定义快照与执行结果；L3 客户端区分「未发出」「结果未知」「业务错误」。
  - 持久化 request 幂等表的测试（同体重放、异体冲突、并发同 ID）。
  - 状态迁移入口的合法 / 非法迁移测试。
  - 冻结 DDL 防漂移测试。
- **`make up` 全部服务 healthy**（含 gateway 新入口与 gateway 迁移），随后 `make test-p1` 仍通过。如果宿主环境有其他 compose 栈导致冲突，先 `make down` 再跑；不要删除任何数据卷以外的东西，删卷前确认它属于本项目的测试库。
- `make lint`、`make test` 全量通过。

## 5. 交付

- 继续在 `feat/l2-foundation` 本地提交，不 push；提交信息末尾 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`。
- 更新 `docs/progress/W0-foundation.md`：逐条列出 P0 / P1 的处理方式与证据；给 W1–W4 的最终接口约定（每个任务可改的文件、必须使用的接口）。
- 最终回复保持简短：完成项、退出命令结果、未完成项、待决。
