# ADR-004：L1 流式协议与录制/回放

日期：2026-10-25。状态：已决策（C1e 实现）。

## 背景

L1 agent 通过 SSE（Server-Sent Events）向 BFF/Frontend 推送流式事件。PRD §6.5 定义了 SSE 事件格式（`metadata`、`updates`、`custom`、`error`），§6.6 定义了回放机制（`LLM_MODE`、请求键、ID 归一化）。

C1e 是本项目的 L1 输出校验与录制/回放组件，承担三个职责：
1. respond 节点：对非固定模板路径使用 LLM 生成自然语言回复
2. verify_output 节点：数字来源校验、长数字掩码
3. 录制/回放基础设施：录制 DeepSeek API 调用用于评测回放

## 决策

### 1. 流式协议

- 复用 PRD §6.5 的 SSE 事件格式：`metadata`（会话元数据）、`updates`（回复与中断）、`custom`（卡片 payload）、`error`（异常）
- C1e 在 `updates`（`messages` 事件）前插入 `custom` 事件推送 UICard 列表
- 卡片不进 AgentState，经 SSE `custom` 事件推送，前端按卡片 ID 去重
- 事件推送顺序：① 中断 → `updates`（`__interrupt__`）② 最终状态 → `updates`（`values`）③ **卡片** → `custom`（`cards`）④ 回复 → `updates`（`messages`）⑤ 异常 → `error`

### 2. 回放方案

采用**请求键哈希**方案（PRD §6.6）：

```
key = sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))
```

- `canonical()` 对易变 ID 做正则替换为 `<IDn>`，种子固定 ID 用白名单保留
- `model` 和**全部**请求参数（`temperature`、`max_tokens`、`extra_body` 等）都参与计算
- 录制文件路径：`evaluation/replay/<profile>/<case_id>.jsonl`
- `LLM_MODE` 环境变量控制：`live`（默认）、`record`、`replay`
- 回放未命中直接报错（`REPLAY_MISS`），不降级到 live

### 3. 录制格式

每条记录一行 JSON：`{key, model, params, response}`。

文件头（第一条记录）存储元数据：
```json
{"schema_version": "<sha256>", "profile": "b1", "tools": [...], "systems": {...}}
```

回放时校验 `schema_version`，不匹配→ `REPLAY_STALE`。

录制内容**禁止**包含 JWT、服务令牌、API key、Authorization 头。测试必须扫描录制文件验证。

### 4. 回放与图执行

- `LLM_MODE=replay` 下，整个图执行仍走正常路径，LLM 调用被 replay 拦截
- 录制按 `(profile, case_id)` 隔离，评测脚本按测试用例建不同 `case_id`
- `replay_schema_version` 冻结时机：功能冻结后、录制前

## 后果

- L1 的 DeepSeek 调用可被确定性回放用于 P3 评测
- 录制文件与评测脚本分离，评测脚本不依赖 live API
- 回放模式下缺录制直接报错，评测前必须确保录制文件存在

### 5. 掩码例外与 PRD 偏差（E3 补充，2026-10-25）

PRD §6.7 1a 要求对 ≥8 位数字「一律」掩码。实现中发现以下必须保留的例外：

| 例外 | 理由 |
|------|------|
| ISO 日期（`YYYY-MM-DD`、`YYYYMMDD`） | 日期不是账号/敏感数字，掩码后「****-10-25」不可读 |
| 带货币标记的金额（`¥…`、`￥…`、`…元`、`…块`） | 金额前已由 OutputVerifier 做过事实校验，掩码后用户无法核对 |

此外，初始实现使用 `\b\d{8,}\b` 依赖 `\b` 词边界。Python 3 的 `\w` 包含 CJK 字符，因此紧贴中文的数字（如「卡号6222021234567890」）不触发词边界，不被掩码。修复方案：

- 用 `\d+(?:[ -]\d+)*` 匹配数字串（允许单个空格/连字符分隔），统计实际位数
- 不依赖 `\b`
- 紧贴中文、标点、行首行尾的数字一律命中
- 掩码输出为 `****` + 后 4 位数字（去除内部分隔符）

## 相关

- PRD §6.5 SSE 事件格式
- PRD §6.6 LLM_MODE 与回放请求键
- ADR-002 DeepSeek 配置与 P0 实验
- C1e 任务书 `docs/plans/briefs/C1e-output-replay.md`

Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>