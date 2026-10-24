# C1e 验收：输出校验、长数字掩码、确定性卡片与录制回放（2026-10-25）

- 分支 `codex/c1e-output-replay`，基线 main 0472689，之后同步 main fe829d0。
- 任务书 `docs/plans/briefs/C1e-output-replay.md`，以末尾的协调者裁定 E1–E8 及「E3 补充」为准；设计笔记 `C1e-design-notes.md`。
- 新 ADR：`docs/decisions/ADR-004-streaming-replay.md`、`ADR-005-frontend-choice.md`（只记录前端暂停）。
- 实现：本机 Claude Code CLI 的 DeepSeek worker（SID ff2fdf9f），Task 0–8 后又做了 2 轮修复。
- 独立评审：DeepSeek 只读 worker（SID 015e9dc1），报告 `docs/reviews/C1e-output-replay-review.md`。首轮 REQUEST_CHANGES（P1×3、P2×3），修复后复审 APPROVE。

## 范围

- **录制回放**：
  - 请求键为 `sha256(model‖canonical(messages)‖canonical(tools)‖canonical(params))`，所有参数都参与计算，不排除任何一项。
  - 易变 ID 按 PRD 正则归一化，种子 ID 走白名单不替换；命中后反向映射回原 ID。
  - replay 模式下缺少录制直接报错，不会回退到 live。
  - 录制文件会清洗 JWT、令牌和 Authorization，但保留 usage 统计字段。
- **输出校验**：抽取回复里的金额（含 ¥、元，以及本组件自行实现的中文数字解析）、百分比、日期和卡尾号，与本轮 reads、decision、execution 中的事实比对。不一致时允许 LLM 改写一次，改写结果要重新校验并掩码；仍不通过就改用固定模板（共 22 个）。不会把未执行或状态不确定的操作写成成功。
- **掩码**：先做 NFKC 归一化并删除零宽字符，再匹配 ≥8 位数字。数字之间允许有分隔串（由空白或连字符组成，长度不超过 3），紧贴中文时也能识别，替换为 `****` + 后 4 位。只有两类例外：ISO 日期，以及带货币标记的金额。与 PRD「一律」的偏差已记入 ADR-004。
- **卡片**：UICard/CardAction 是 L1 内部模型，只从 ReadEnvelope.data 子集或 Decision/ExecutionResult 生成。CardAction 只有 4 种 kind，且不带可见文字。卡片上限 5 张；LLM 只负责在候选卡片 ID 中挑选。

## 协调者亲自核验（5d1c4de）

| 命令 | 结果 |
|---|---|
| make lint | 324 files |
| make test | 2671 passed / 646 skipped |
| .venv/bin/pytest tests/agent -q | 455 passed / 33 skipped |
| make test-agent-db | 34 passed |
| make test-replay / test-verify / test-cards | 47 / 70 / 19 passed |
| make contracts-check / actionlint | PASS / exit 0 |
| make demo | exit 2（依赖未完成的 L2/BFF，如实登记） |

掩码实测（含「卡号6222021234567890」、全角数字、零宽字符、多空格、带空格连字符）均符合预期；中文数字实测「八百元」= 80000 分，「三万五千元」= 3500000 分。

红绿核验：

| 临时改动 | 结果 |
|---|---|
| 换回 61c271a 的 verify.py | 掩码测试 5 failed |
| 换回 17204bd 的 verify.py | 相关测试 13 failed |
| 回放键去掉 model | replay 测试 1 failed |

## 已知问题 / 未覆盖

- 中文金额中的角、分会被丢掉，例如「两千元五角」只抽出 2000 元，最大误差 0.9 元。
- 中文与阿拉伯数字混写（如「一千200元」）解析不完整。
- 种子 ID 白名单目前写死为常量，种子数据实现后改为从种子源读取。
- `make demo` 仍为 exit 2；与真实 L2 G4/G8 的联调尚未进行。

过程中 worker 两次尝试 git push，均被启动器拦截。
