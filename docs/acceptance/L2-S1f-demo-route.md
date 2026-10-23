# S1f 验收：G22 按 DEMO/EVAL 开关条件挂载（2026-10-24）

- 分支 `feat/l2-demo-route-guard`，基线 main aa42a0c；实现提交 38e382c、修复轮 1 d169f52（DeepSeek V4 Pro 无头 Claude Code worker）。
- 任务书 `docs/plans/briefs/S1f-demo-route-mount.md`；进度与红绿证据 `docs/progress/S1f-demo-route-mount.md`。

## 协调者亲自复跑（d169f52）

| 命令 | 结果 |
|---|---|
| make lint | exit 0，175 files already formatted |
| make test | 408 passed / 302 skipped |
| make test-gateway | 173 passed / 254 skipped |
| make test-gateway-db | 17 passed（真实临时 PG） |
| make test-shared-runtime | 59 passed，零 skip（真实 PG + Linux 权限） |
| make contracts-check | PASS |
| actionlint 1.7.12 | exit 0 |

（38e382c 上首次复跑六退出同样全部 exit 0：406/302、171/254、17、59、PASS。）

## 独立评审

DeepSeek 只读评审 worker：`docs/reviews/L2-S1f-review.md`，APPROVE，P0/P1 无；两条 P2（方法匹配、嵌套 router fail-closed）已在 d169f52 修复。

## 未覆盖

- G22 客户列表业务内容属 W1，本组件只验收挂载与凭证边界（off 404；on 无凭证 401，F 凭证到 W1 stub 503）。
- 未运行 make up / test-integration（本组件不改 compose、镜像或 L3）；远端 CI 以 PR 实际运行为准。
