# P1 GitHub Actions 诊断（只读排查）

日期：2026-10-23；分支 feat/l3-core，当前提交19be409。未修改业务或CI配置，未重新触发CI。

最近三次 push 均为相同失败：

| Run | unit | p1-postgres |
|---|---|---|
| https://github.com/SJeffZhang/Safebank/actions/runs/39925868490 | 测试、ruff、format通过 | make up失败，后续5个验收步骤跳过 |
| https://github.com/SJeffZhang/Safebank/actions/runs/39925660369 | 测试、ruff、format通过 | 同上 |
| https://github.com/SJeffZhang/Safebank/actions/runs/39925600593 | 测试、ruff、format通过 | 同上 |

共同根因日志（bank与agent各一条）：

```text
psql: error: /docker-entrypoint-initdb.d/01-roles.sql: Permission denied
```

scripts/keys.py:21 将生成文件设为0600；docker-compose.yml:134与152将SQL只读绑定到初始化目录，文件包含数据库角色口令。官方镜像启动脚本实际先在第356行gosu降权，再在第376行处理init文件，第209行使用psql -f读取SQL。Linux绑定挂载保留宿主所有者和权限，容器postgres用户不能读取该0600文件。已有本地数据库卷跳过首次初始化，不能代替Linux全新卷验收。

修复方案（用户已确认，实施及远端CI已通过）：增加仅用于Postgres启动的权限适配脚本，root阶段从只读挂载读取SQL，复制到容器私有tmpfs初始化目录，设为postgres所有且0600，再交回官方entrypoint；保留宿主.secrets为0700/文件0600，SQL不进镜像，不放宽为全员可读。两个Postgres服务使用相同流程。

验收：在独立临时环境以全新卷复现与验证首次启动、双库角色/CONNECT权限；重复启动保留数据；实际宿主密钥权限不变；make test-p1、并发、一致性、网络/HTTP恢复通过；推送获授权的修复后确认GitHub两项job均绿。测试不删除现有银行卷。

当前判断：一个初始化根因，两个容器报错，三个提交重复触发；未看到银行业务断言失败。被跳过的远端集成测试仍未验证，修复启动后需继续检查，不能保证没有后续错误。

后续结论：70dcabe修复已推送，[新CI](https://github.com/SJeffZhang/Safebank/actions/runs/39929405919)两项job均success，初始化与此前跳过的全部PG验收通过。原诊断保留作失败证据，最终验收见CI-P1-fix.md。
