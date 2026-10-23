# S1e 独立只读审核

2026-10-23。审核者 `review_s1e_orphans`。审查范围：`c381a427b27893dcd2924bd5ba332fa6d4413262..5842d44703b43811daa402b2afde08e63d1d1d28`，worktree `/Users/jet/.codex/worktrees/safebank-wave1-state/WeBank`。结论固定于该 HEAD，未修改工作树、索引或 HEAD。

**规格：FAIL。质量：REQUEST_CHANGES。** 发现 1 项可复现 P2，修正后需定点复核；未发现 P0/P1。

## 规格审核

依据根目录最新 `docs/plans/briefs/S1e-orphan-jobs.md`、PRD §7.4/§7.10 的兼容增量、ADR-008，以及实施/验收报告。先读提供的完整 U10 diff；初次工具合并输出截断，后续按 diff 行区间补读被截断部分。为确认新增 guard 是否满足完整 scope 约束，额外读取未变私有 `_claims`（state.py:309–326）及普通执行入口（state.py:221–370）；为定向真实 PG 探针读取既有 tmpfs fixture。未扩展到 W1/W4 业务实现。

符合项：

- 新增独立不可变 `OrphanExecutionIdentity`，正整数 attempt、固定 origin；旧 `ExecutionIdentity` 与公共签名未改变，普通 `_locked_execution` 明确拒绝孤儿身份，无 revision=0/None 或自动降级。
- 两接口使用调用者已有连接/事务；源码未 commit、换连接或发 HTTP。plan→job→claims 加锁顺序明确，身份、attempt、job status、零 evaluation 均校验；最终 job UPDATE 再带 NOT EXISTS 和 owner 条件。
- UNKNOWN 仅更新 plan/job 状态、reason、更新时间；提交证据、期限、cap、attempt、claims 保留。终态接口要求 plan/job 均 UNKNOWN，先验证结果、条件更新，再归档 plan/消费 claims。
- COMMITTED 要求匹配 plan 的 EXECUTED 结果、提交时间及全部成功步骤；CANCELLED 接受 None 或无成功步骤的 FAILED 结果。CANCELLED/SUPERSEDED 原话不变。
- 修改范围仅 state、新增专属测试和证据文档；无迁移、表、schema、依赖或其他业务变更。旧终态校验/消费提取为私有函数，检查未发现普通路径语义回归。

### [P2] 空 scope 与零 claims 可穿过两个清理入口

位置：`services/gateway/app/state.py:812–820`（重点 813），复用 `_claims` 在 `:322–326`。

任务书要求 `scope_ids` **非空且不得重复**，畸形旧行必须 409，不能自动修复。新增 shape guard 只拒绝非 list 或含非法元素的 list；`[]` 合法通过。若同一 plan 同时没有 claim，既有 `_claims` 的两个空集合相等，且 `any()` 为 false，因此也通过。这样无任何授权 scope/claim 的遗留行可以被转 UNKNOWN，之后终态归档还会把 `utterance_ids` 中 ACTIVE 原话改为 CONSUMED。

真实 PostgreSQL 定向探针：沿用既有 `_provision()`（显式 PGDATA、同路径 tmpfs、唯一容器）与专属 orphan fixture，仅将 scope_ids 改为 [] 并删除该 plan 的 claims。连续调用 mark、CANCELLED 归档，实际输出：

```text
EMPTY_SCOPE_MARK_ACCEPTED UNKNOWN UNKNOWN [] []
EMPTY_SCOPE_ARCHIVE_ACCEPTED FAILED FAILED CONSUMED []
```

探针 exit0，数据库改动只在临时容器中，结束已清理该唯一容器。该结果证明规格拒绝条件缺失，并非依靠 mock 推断。未发现借此重新 prepare/execute 的路径，但它违反明确的旧行 fail-closed/非空 scope 边界，阻塞本组件批准。

修正建议：在孤儿专属 shape guard 加入空列表拒绝，不扩大普通路径；分别为两个入口补 `scope_ids=[]` **且零 claims** 的真实 PG 用例，断言 409 STATE_CHANGED 和调用前后完整 snapshot 相同。现有 `test_orphan_unknown.py:120–125` 的 `missing_scopes` 留着原 claim，因此只覆盖集合不相等，无法捕获此组合；终态用例也需独立覆盖。

## 质量与证据范围

已有测试源码覆盖四 live 状态、旧空/非空提交字段、错误身份/attempt/status、缺失/额外/已消费 claims、重复/畸形 scope、有 evaluation 拒绝、UNKNOWN 幂等、旧 revision 门禁、结果错误、回滚和保存点隔离。最终 NOT EXISTS 的同连接真实插入故障注入会在删掉该条件后改变真实行，具备针对性。两个独立连接的 CAS/归档测试检查只有一次转换，evaluation 双顺序测试检查后续读到的新状态；后者使用见证 writer，并未证明真实 W3 append_evaluation 接线，报告对此如实限定。

本审核未重复整套六退出；协调者正在亲自执行。只运行上述具体缺口探针。探针首次因未设源码 PYTHONPATH 在 import 前失败；补上后普通沙箱在本地 socket bind 被拒；授权提升执行后成功。未将这些环境失败当成实现测试通过。未运行或声称通过任何真实 W1 G3/G14 HTTP、W4 L3 query/reconcile/ACK/锁丢失矩阵；客户锁真实性、HTTP 身份和终态真实性仍由对应调用者证明，helper 本身没有伪造证明。

完成复核时 worktree 仍为 HEAD `5842d44703b43811daa402b2afde08e63d1d1d28` 且 `git status --short` 为空。仅本审核报告写入根目录；未提交、推送、开 PR、操作业务数据卷或派生 agent。
