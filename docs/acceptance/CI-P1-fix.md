# P1 CI 初始化权限修复验收

日期：2026-10-23；分支 feat/l3-core。用户明确确认诊断方案并授权推送。

根因与原始三次失败见 [诊断](CI-P1-diagnosis.md)。修复代码为 scripts/postgres-entrypoint.sh：root阶段将只读输入SQL复制到容器私有tmpfs，目录0700、文件0600、所有者postgres，然后exec官方entrypoint。宿主.secrets的0700及SQL/密钥0600保持；SQL不进入镜像，不暴露给其他服务。

| 实际命令 | 本地结果 |
|---|---|
| sh -n scripts/postgres-entrypoint.sh | 通过 |
| .venv/bin/python -m scripts.postgres_bootstrap_acceptance | 双库均在全新原生Linux卷复现原失败；修复后初始化/角色隔离/权限/重启数据保留全部通过 |
| make up | 已有卷保留，双库及服务健康 |
| make test-p1 | 独立测试库空库迁移、重复升级及33项真实PG全部通过 |
| make test-p1-concurrency | 5 passed |
| make test-p1-consistency | 17 passed |
| make test-integration | 网络、角色、凭证、HTTP转账和实际L3重启幂等恢复通过 |
| make test / make lint / make contracts-check | 116 passed, 33 skipped（PG目标单独通过）；lint和冻结契约通过 |

原始输出在 ci-bootstrap/。独立卷回归用外部uid=12345持有的0600 SQL，避免macOS绑定挂载或已有银行卷掩盖Linux权限问题；使用新生成的合成测试凭证。所有容器/卷随机独立命名，清理只针对本次创建的资源，不删除应用卷、不打开Docker Desktop。

CI增加 make test-postgres-bootstrap，确保此首次部署边界永久覆盖。修复未改变银行业务、金融阈值、数据库迁移或共享契约。远端CI结果将在推送后补记；本地通过不等于远端通过。
