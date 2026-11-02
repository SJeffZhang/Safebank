# 仓库展示与复现检查 · 2026-11-02

代码基线：`7ceda71`，当前检出 `feat/form-authorization`。本轮工作重写对外文档，补演示 Compose 叠加配置；未修改金融规则、业务接口或数据库迁移。

## 自动化结果

| 命令 | 实际结果 |
|---|---|
| `make test` | 3,552 passed / 1,065 skipped，30.86 秒 |
| `cd frontend && npm test` | 43 个文件、400 项通过，13.77 秒 |
| `cd frontend && npm run test:bff` | 16 项通过，0 失败、0 跳过 |
| `cd frontend && npm run build` | TypeScript 与 Vite 构建通过，53 个模块 |
| `make lint` | ruff check 通过；497 个文件格式检查通过 |
| `make contracts-check` | `PASS: contracts v1 match frozen schemas` |
| `docker compose -f docker-compose.yml -f docker-compose.demo.yml config --quiet` | 通过 |
| `.venv/bin/python -m scripts.try_agent --help` | 通过；文档参数使用 `--customer-id` |

条件跳过项未计为通过；BFF 使用本地测试上游。这些耗时是本机工具运行时间，不是业务性能基准。

## 文档与演示配置

检查新旧 Compose 解析结果：只允许 Agent 接收模型 API key；演示配置保持 B2。网络与数据卷定义相同，每个服务的网络、发布端口、挂载、只读设置、能力限制、安全选项和启动命令均未改变。清除 API key 且指定空 env 文件后，配置解析按预期失败。

检查本次对外 Markdown 的本地链接与图片路径、命令对应的 Makefile / package.json / CLI 参数，以及横幅 SVG 的 XML 结构。初次检查有一条指向本记录的待建链接，本文件补齐后复核。横幅已渲染预览；截图沿用仓库内合成预览图片，并明确标注其来源。

语料计数从 11 个 JSONL 文件重新读取，共 405 条。README 中的真实业务结果逐项引用既有 JSON，不把这些组件记录合并为一轮完整六场景 live。

## 未运行与范围

本轮没有发起 DeepSeek 请求，没有启动、重建或重启已有 Compose 栈，没有更改数据库、数据卷、实际密钥或服务器。新演示配置通过静态边界检查，尚未在全新机器完成完整部署验收；部署步骤由现有脚本与历史验收交叉核对。

本轮没有重跑完整真实 PostgreSQL CI、全量攻击对照或断网回放。原有组件的合并、冻结与赎回专项状态继续以其验收记录和维护日志为准。

## 合并主线后的检查

2026-11-02，PR #115 候选 `f324c52` 同步主线 `ac0884e` 后，单元 4,977 passed / 1,169 skipped（35.90秒）、前端 494 passed / 50文件（13.73秒）、BFF 16 passed 及构建通过。修订首页指标为此基线；旧基线结果仍保留在本记录前文。补充主线已存在的赎回 live、生产 record/replay 与 make demo 驱动说明。

完整本机CI首次在bootstrap步骤前因部署检出旧venv指向临时Python而缺encodings停止，尚未操作数据库；保留旧环境并用本机Python3.12重建锁定依赖后重跑最终head。完整CI及最终合并状态以[PR #115](https://github.com/SJeffZhang/Safebank/pull/115)回执为准。
