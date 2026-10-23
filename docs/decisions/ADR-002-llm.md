# ADR-002：DeepSeek P0 实测与接入配置

日期：2026-10-23。状态：P0 配置已决策；生产契约与多轮持久化须 P1/P2 单独验收。

固定模型 `deepseek-v4-pro`，strict 端点 `https://api.deepseek.com/beta`。
采用 `thinking.type=disabled`、temperature=0、单个 strict 工具 + 指定 tool_choice。
E2 原配置开启思考时 30/30 返回 HTTP 400：Thinking mode does not support this tool_choice。
按 PRD §6.6 的第一级回退关闭 plan 思考，额外 30 次独立验证全部通过。
不擅换模型，不因失败改用 Flash；Flash 仅未来多模态。

| 实验 | 样本 | Schema合法 | HTTP400 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|
| E1-gather | 90 | 90 | 0 | 1190 | 1653 |
| E1-understand | 90 | 90 | 0 | 1670 | 2544 |
| E2 | 30 | 0 | 30 | 182 | 289 |
| E2-fallback | 30 | 30 | 0 | 3896 | 4536 |
| E3 | 30 | 30 | 0 | 1172 | 1783 |
| E4 | 30 | 30 | 0 | 1266 | 1824 |
| E5 | 30 | 30 | 0 | 3621 | 4218 |

共 330 次正式 HTTP 请求（另有 2 次 smoke：首次枚举缺 type 被拒，修复后成功）。
E1 每类3提示各30次；E3 null、E4 $def、E5含13种助手写工具的 anyOf 均被接受。
E6 的耗时与 usage 原值保存在每个 summary.json/results.jsonl。p95 使用排序后的最近秩，包含代理和公网，不代表 L2 性能。
LangChain 1.1.1 + strict + 显式 beta + 关闭思考真实调用成功，见 [适配器结果](../acceptance/langchain-live.json)。

实验 schema 是 P0 的 PRD 形状探针，非生产 Pydantic contracts；E1 仅代表理解与 get_accounts，E5 调用只要求 transfer，尚未逐工具验证语义/金额来源。
E3支持null不代表已完成哨兵互转；现行PRD哨兵/引用协议保留，P1应从真实模型生成并重跑契约回归。
没有多轮思考工具调用，因此不声称已经解决 reasoning_content 回传。当前配置无需该回传；更改思考模式必须重新实测。
内存 checkpoint 中断恢复与 runtime context 脱敏验证见 tests/test_langgraph.py；Postgres saver 持久化与恢复进入相应组件验收，不冒充已测试。

证据：[主实验](../acceptance/deepseek-20261023T062843Z/summary.json)、[回退](../acceptance/deepseek-20261023T063106Z/summary.json)。
官方来源：[tool calls](https://api-docs.deepseek.com/guides/tool_calls/)、[模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)，本日核对。
