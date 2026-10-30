# Live 驱动保护组件 A（2026-10-30）

输入：用例TSV、干净栈部署路径、PIN文件（不打印）、可选TRY_AGENT_TIMEOUT_SECONDS。
输出：每条前后快照、脱敏trace、summary.tsv、部署SHA/种子与时钟metadata。
退出75立即停批次（402/403/429）；124超时停止，需先核对在途计划。模型客户端max_retries=0已存在。

验证：原驱动新增5测试全部失败（额度返回0而非75、静默返回0而非124）；新驱动通过。
原批处理新增2测试失败，新批处理通过；旧码核验备份后还原。
`pytest tests/try_agent -q`：70 passed。
`bash -n .../run_full2.sh .../local_ci.sh`：通过；ruff与diff检查通过。
其中首次红测试夹具等待stdin曾手动中断；之后改为0.5秒自退出，红绿结果来自后者。
未调用模型、未部署。精确head全量CI和live另行记录后才合并。

Post-commit acceptance: exact 4b1859a full local CI PASS (unit 3504 passed/979 skipped;
all configured PG/integration targets). Clean stack actual model balance query took 8s,
reported 8200.00 yuan; business snapshot unchanged. Existing CLI returned CLARIFY (2),
so the record is an observed response, not a business-success assertion. PR #98 merged
0caeb03. Main redeployed afterward. Public progress metadata published and curl verified.
