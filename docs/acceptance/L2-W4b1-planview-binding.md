# W4b1 PlanView / Approval Binding 验收

2026-10-24，协调者亲验。业务候选c317f82e4487183e1359c08c515aa4aa0fed7ad6，最新main b281023合入为e6a9ee6478ddfe721c609043e54490bc30e9f164；只有AGENTS文档冲突，双方历史逐条保留。agent与依赖相对main无业务diff。

交付确定性PlanView/content_hash、显式服务端HMAC配置/恒时验证与短事务审批创建库。缺配置503；不使用默认密钥。真实PG证明归属/最新评估、并发同evaluation同ID、单会话supersede、撤销/过期/live job/CLAIMED（含无job）拒绝、幂等坏MAC拒绝、session-first锁序。库不commit/HTTP/执行L3。非空finding与真实G4组合由W3b补验。

原DeepSeek独审1f1c599c-1037-4abc-8862-87d62758e07a先REQUEST_CHANGES，原实现11b6cf9f-1aaf-4433-a7ae-9e46d13bf899逐条修复。协调者额外真实PG复现CLAIMED无job、坏MAC快速返回、裸库锁序问题后再次修复；最终原独审APPROVE，无未闭P0/P1/P2。报告保留历次结论。评审只亲跑纯函数/探针，真实PG由协调者独立亲跑。

| 合并main后实际命令 | 结果 |
|---|---|
| make setup / make lint | 78依赖 / 290格式检查通过 |
| .venv/bin/pytest tests/agent -q | 212 passed，零skip |
| make test-gateway / make test-gateway-db | 1962 passed+501 skipped / 17 passed |
| make test | 2403 passed+561 skipped；跳过项未算通过 |
| make test-w4b1 | 57 passed零skip/xfail，其中34纯函数、23真实临时PG |
| make test-w3a / make test-w2 / make test-obligations | 45 / 246 / 30 passed |
| make contracts-check / actionlint | PASS / exit0 |

日志：/private/tmp/sbg-codex-w4b1-r2-20261024、/private/tmp/sbg-w4b1-main-combination-20261024。PG均独立新建tmpfs fixture，仅清理其临时容器；长期卷/业务数据未重建/reset/TRUNCATE。新增CI只向既有p1-postgres追加W4b1专项，job名不变；旧probe占位断言最小改缺配置503，已批准。

边界：生产runtime/G6/G7/G9/G10/G17/OTP尚未接线（W4b2）；完整G4待W3b；G8/UNKNOWN恢复待W4c；问卷实体展示缺权威元数据仍fail-closed，留P4。L1真实PG/SSE/输出/三层转账未闭合，前端/BFF暂停，不能据此宣称P2或P3完成。最终发布仍须完整head push与pull_request各unit/p1-postgres四CI成功才merge。
