# 任务书 W0 第四轮：开工前的最后修补

第三轮定点复核结论为「有条件通过」，见 `docs/reviews/L2-W0-architecture.md` 末尾「第三轮定点复核」。只修下面 4 项，不扩展范围。

1. **R1 JWT 公钥加载**：Settings 增加公钥路径配置（与 compose / `.secrets` 现有挂载方式一致），`serve()` 启动时加载并传给 `caller_dependency`；路径缺失或读取失败时拒绝启动。测试：缺公钥时启动失败；有公钥时带合法 JWT 的请求能通过鉴权。
2. **R2 pyyaml 进主依赖**：把 pyyaml 从 dev 依赖移到主依赖（精确版本不变），重新导出 `requirements*.txt` 并按仓库现有流程更新 wheels；`docs/third-party.md` 同步。确认 gateway 镜像内 `import yaml` 成功。
3. **R3 action_kind fail-closed**：影子状态中找不到卡（或找不到原值无法比较）时，不得默认 TIGHTEN；改为抛出明确错误（由调用方按实体不可见处理）。测试覆盖「卡不存在」「把限额调到极大值」两种情况。
4. **R4 outflow 口径（指挥裁定，依 PRD §5.0）**：只有 ActionKind 为 `TRANSFER_EXTERNAL` 的转账，以及目标为他人收款人的 `schedule_transfer`，计为对外转出；本人互转（`TRANSFER_SELF`）、基金申购、赎回都**不**计入。修正 `policies/tools.yaml` 中的值；如果 schedule_transfer 的本人 / 他人需要按参数判定，就在注册表里提供与 `action_kind` 同风格的判定函数。测试覆盖三类。

约束同第三轮任务书（禁止删除或重建任何数据卷；不 push；可改范围相同，另允许改 `services/gateway/app/main.py`、`config.py`）。

退出：`make lint`、`make test-gateway`、`make test-gateway-db`、`make test` 通过；`make up`（现有卷）全部 healthy 后 `make test-p1` 通过。本地提交（末尾 `Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>`），更新进度文件，简短回复。
