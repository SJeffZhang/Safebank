# L2-parser-coverage-b

**任务**：在 coverage-a 基础上继续扩展 gateway G3 原话解析器，使 2 类此前被判 `INTENT_UNCLEAR`（→ FORM_REQUIRED）的写请求产出正确的 SCOPED scope：

1. 带具体锚点的周期转账（`每月20号给赵六转200元`、`每周一给妈妈转100元`）。
2. AA 分账请求（`昨晚聚餐400元，我和小林、小周、小陈AA`）。

**分支**：`feat/l2-parser-coverage-b`（worktree GW-parser-b）

## 结果

| 原话 | 预期 scope | 解析器改动 |
|---|---|---|
| 每月20号给赵六转200元 | SCHEDULE_CREATE，fp_zl 20000，period=MONTHLY，first_run_at=2026-10-20T08:00:00+08:00 | 是：`每月N号` 由 future 改为完整 schedule，锚点经 `_first_run_at` 编码为 first_run_at |
| 每周一给妈妈转100元 | SCHEDULE_CREATE，fp_mom 10000，period=WEEKLY，first_run_at=2026-10-19T08:00:00+08:00 | 是：新增 `每(周\|星期\|礼拜)X` 模式 + 工作日映射 |
| 明天给赵六转200元（无锚点） | INTENT_UNCLEAR，missing first_run_at | **否**：既有 future 规则保留 |
| 昨晚聚餐400元，我和小林、小周、小陈AA | SPLIT_CREATE，total_cents=40000，account_id=acc_zs，participant_payee_ids=[pye_xl,pye_xz,pye_xc]，include_self=True，无转账 | 是：新增 `_split_participants` 子句 + parse() 内合并为 create_split_request |

## 实现要点

- `_first_run_at(sim_now, period, anchor_day, weekday)`：月度取下一个 `anchor_day` 的 08:00 本地时间（跨月/超月长返回 None），周度取下一个工作日的 08:00。Schedule 契约只经 `first_run_at` 携带锚点（无 anchor_day/weekday 字段），因此具体锚点是完整请求而非 FORM_REQUIRED。
- `_clause` 的 future 分支拆为三路：只有裸未来（明天/后天/定时）仍置 `future=True`；月度/周度锚点改为 `schedule_transfer` + `{period, anchor_day|weekday, continuous}`，不置 future。
- `parse()` 主循环：`anchor_day`/`weekday`/`continuous` 为内部锚点槽，不再写入 `scope.fields`；非 future 且非 derived_template 的 schedule_transfer 用 `_first_run_at` 计算并 `put("first_run_at", …)`。
- 分账合并：恰好 1 个 `_split_participants` + 1 个裸转账（总金额）且无其它子句；总额目标解析到任何账户/收款人即拒绝（是聚餐描述而非收款人）；每个参与人须精确匹配唯一收款人；去重后 1–20 人；默认活期账户；替换为单个 `create_split_request` 命令。

## 安全性（未放宽）

- 未知/歧义参与人（`我和小王AA`、`我和王AA`）仍非 SCOPED。
- 总金额句提到收款人（`王五400元，我和小林、小周AA`）拒绝——金额只来自文本、目标必须是聚餐描述。
- 否定（`不要AA`）、疑问（`…吗`）、引号（`他说“…”`）均不产生 scope——共 6 条攻击回归用例。
- 金额从不编造：分账总额来自文本中的裸金额句；周期转账金额来自文本。
- 无锚点的周期转账（明天/后天/定时）仍 FORM_REQUIRED，未放宽。

## 测试

新增 `tests/gateway/w1/test_parser_coverage_b.py`：3 条正向（月/周/裸未来回归）+ 1 条分账正向 + 6 条攻击回归（parametrize）。

红测阶段（提交 ba26be7）：3 failed / 1780 passed（3 条正向失败，裸未来回归与全部攻击回归当时已通过，因当时一切非 SCOPED）。

修复后（提交 017e3ed）：1782 passed。

## 门禁（全部通过，实际运行）

- `make lint`：All checks passed；`ruff format --check` 419 files already formatted
- `make test`：3276 passed, 879 skipped
- `make test-w1`：1782 passed
- `make test-w2`：256 passed

## 变更文件

- `services/gateway/app/utterance/parser.py`（+147/−5：`_WEEKDAYS`、`_first_run_at`、weekly/split 子句、split 合并、create_split_request 分支、first_run_at 计算与内部锚点槽过滤）
- `tests/gateway/w1/test_parser_coverage_b.py`（新增，124 行）

## 提交

- ba26be7 红测
- 017e3ed 解析器实现
- 0376d5f ruff format

未 push。
