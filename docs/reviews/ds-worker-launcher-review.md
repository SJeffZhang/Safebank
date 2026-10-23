# ds-worker.sh 修复评审（3 行 set -u 修复）

- 范围：`scripts/ds-worker.sh` 相对 HEAD `8dfdec15d08e441a758f70838f682847117427e2` 的未提交 3 行改动（HEAD→工作区，`git diff --stat` 仅此 1 文件 +3/−2）。初稿误把 diff 索引里的文件旧 blob `5446407` 当作 HEAD——`5446407` 是脚本在 HEAD 处的内容 blob 哈希，不是提交号，此处已更正。
- 结论：**REQUEST_CHANGES**（1 条 P1；另有 1 条 P2 为预存在、非本次引入）。

## 修复本身（正确）

- 原 bug：`extra=()` 空数组在 macOS Bash 3.2 + `set -u` 下展开 `"${extra[@]}"` 报 unbound variable。修复改为 `worker=(claude -p "$(cat "$prompt")")`，恒有 ≥2 元素，fresh 路径不再展开空数组。`worker+=(...)` 在 Bash 3.2（≥3.1）合法。
- resume 原样传递：`worker+=(--resume "$resume")`，引号包裹，条件 `[ -n "$resume" ]` 与旧 `[ -n "$resume" ] && extra=(...)` 等价。
- 未引入 `--dangerously-skip-permissions`：仍为 `--permission-mode acceptEdits` + `--allowedTools/--disallowedTools`。
- `env -u` 列表与 allow/deny 白名单（第 12–25 行）未变：仍清除 ANTHROPIC_BASE_URL / AUTH_TOKEN / API_KEY / MODEL / DEFAULT_*_MODEL / CLAUDECODE / CLAUDE_CODE_ENTRYPOINT；impl 禁 make up/down/test-p1*/test-integration、docker volume、git push，review 禁 Edit/commit/make/docker。

## P1：prompt 路径解析时机改变（本次修复引入）

- 证据：旧 HEAD 第 28 行先 `cd "$wt"`，第 34 行才 `$(cat "$prompt")`；新代码第 26 行 `worker=(claude -p "$(cat "$prompt")")` 在第 29 行 `cd "$wt"` **之前**求值。
- 失败场景：`scripts/ds-worker.sh impl ../Fintech2026-wt/W1 docs/plans/briefs/W1-identity.md W1`（任务书位于 worktree 内）时，新代码读 `<主checkout>/docs/plans/briefs/W1-identity.md`（可能不存在或内容不同），旧代码读 `<W1>/docs/...`。读不到时 `cat` 失败，`set -e` 下脚本在数组赋值处即提前退出（旧代码则把空 prompt 继续传给 claude）。
- 影响：仅在 `$prompt` 以相对路径传入时触发；若约定为绝对路径则无影响。当前用法注释与交接文档均未规定该约定。
- 修复建议（任选其一，并写进用法注释/交接文档）：
  1. 把 `cd "$wt"` 移到 `worker=(...)` 之前，恢复「相对路径按 worktree 解析」的旧语义；
  2. 或在 `cd` 前把 `$prompt` 解析为绝对路径：`prompt=$(cd "$(dirname "$prompt")" && pwd -P)/$(basename "$prompt")`；
  3. 或明确约定 prompt 必须为绝对路径 / 相对调用者 cwd，二者不混用。

## P2（预存在，非本次引入，不阻塞）

- 第 7 行 `set -e` 下，第 34–36 行 `env ... claude ...` 返回非零时脚本直接退出，第 37 行 `echo "$name exit=$?"` 只会在成功时打印 `exit=0`，失败时的真实退出码不会被记录。属旧行为，与本次 3 行无关。

## 未确认

- 未能在本沙箱复跑 `/bin/bash`（命令需审批）；数组追加与空数组 `set -u` 行为依据 Bash 3.2 语义与协调者已跑结果判断。

## 复核定稿（第二轮，P1/P2 修复后）

结论：**APPROVE**。

- P1 已修：`scripts/ds-worker.sh:26` `cd "$wt"` 先于 `:27` 的 `worker=(claude -p "$(cat "$prompt")")`，相对 prompt 恢复按 worktree 解析。
- P2 已修：`:31` `status=0`，`:37` `env ... || status=$?`（errexit 对 `||` 左操作数不生效，能捕获非零退出码），`:38` `echo "$name exit=$status"`，`:39` `exit "$status"` 原样返回。
- 非退化核对（本评审独立 fake-claude 实跑，无真实 Claude 调用、无密钥读取）：注入的 ANTHROPIC_BASE_URL/AUTH_TOKEN/API_KEY/MODEL 四变量全部被清除（rec.anth 全 None）；`--resume sess-123` 原样传递（has_resume=True）；从异 cwd 以相对路径 `docs/reviews/ds-worker-launcher-review.md` 作 prompt 时，fake claude 收到该文件首行（prompt_head_ok=True）且 cwd 为 wt，证明 P1 语义恢复；fake claude `exit 7` 时脚本 `returncode 7` 且 stdout `recheck exit=7`。
- `env -u` 列表（:32–34）、无 `--dangerously-skip-permissions`、allow/deny 白名单（:12–25）均未变；diff 仅集中在 :26–28 重排与 :31/:37–39 的 status 处理，无其他退化。
