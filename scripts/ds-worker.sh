#!/usr/bin/env bash
# Launch a headless Claude Code worker routed to DeepSeek V4 Pro (via ~/.claude/settings.json).
# Usage:
#   scripts/ds-worker.sh impl   <worktree> <prompt-file> <log-name> [resume-session-id]
#   scripts/ds-worker.sh review <worktree> <prompt-file> <log-name> [resume-session-id]
# Result JSON: $DS_LOG_DIR/<log-name>.json (fields: result, num_turns, session_id, modelUsage).
set -euo pipefail
mode=$1 wt=$2 prompt=$3 name=$4 resume=${5:-}
logdir=${DS_LOG_DIR:-/Users/zhangshijie/Desktop/Project/Fintech2026-wt/.ds-logs}
mkdir -p "$logdir"

common_ro=(Read Glob Grep Write TodoWrite "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)"
  "Bash(git status:*)" "Bash(git merge-base:*)" "Bash(.venv/bin/pytest:*)" "Bash(.venv/bin/python:*)"
  "Bash(ls:*)" "Bash(cat:*)" "Bash(head:*)" "Bash(tail:*)" "Bash(wc:*)" "Bash(grep:*)")
impl=(Read Edit Write Glob Grep TodoWrite "Bash(make:*)" "Bash(uv:*)" "Bash(docker ps:*)" "Bash(docker logs:*)"
  "Bash(docker run:*)" "Bash(docker rm:*)" "Bash(docker stop:*)" "Bash(docker exec:*)" "Bash(docker inspect:*)"
  "Bash(git status:*)" "Bash(git diff:*)" "Bash(git add:*)" "Bash(git commit:*)" "Bash(git log:*)" "Bash(git show:*)"
  "Bash(.venv/bin/python:*)" "Bash(.venv/bin/pytest:*)" "Bash(.venv/bin/ruff:*)" "Bash(.venv/bin/alembic:*)"
  "Bash(ls:*)" "Bash(mkdir:*)" "Bash(cat:*)" "Bash(head:*)" "Bash(tail:*)" "Bash(wc:*)" "Bash(grep:*)")
deny_impl=("Bash(docker volume:*)" "Bash(docker compose down:*)" "Bash(docker compose up:*)" "Bash(docker system prune:*)"
  "Bash(make up:*)" "Bash(make down:*)" "Bash(make test-p1:*)" "Bash(make test-p1-db:*)" "Bash(make test-p1-concurrency:*)"
  "Bash(make test-p1-consistency:*)" "Bash(make test-integration:*)" "Bash(git push:*)")
deny_ro=(Edit "Bash(git commit:*)" "Bash(git add:*)" "Bash(git push:*)" "Bash(git checkout:*)" "Bash(make:*)" "Bash(docker:*)")

if [ "$mode" = impl ]; then allow=("${impl[@]}"); deny=("${deny_impl[@]}"); else allow=("${common_ro[@]}"); deny=("${deny_ro[@]}"); fi
cd "$wt"
# The CC Switch / Bailian route only serves deepseek-v4-pro; requests that name a Claude
# model (e.g. claude-opus-5) are rejected with 429, so pin every model alias explicitly.
ds_model=${DS_MODEL:-deepseek-v4-pro}
worker=(claude -p "$(cat "$prompt")" --model "$ds_model")
if [ -n "$resume" ]; then worker+=(--resume "$resume"); fi

# Clear inherited Anthropic env so the CLI uses ~/.claude/settings.json (DeepSeek), not the desktop app's account.
status=0
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL \
  -u ANTHROPIC_DEFAULT_HAIKU_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_OPUS_MODEL \
  -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  ANTHROPIC_MODEL="$ds_model" ANTHROPIC_DEFAULT_OPUS_MODEL="$ds_model" \
  ANTHROPIC_DEFAULT_SONNET_MODEL="$ds_model" ANTHROPIC_DEFAULT_HAIKU_MODEL="$ds_model" \
  ANTHROPIC_SMALL_FAST_MODEL="$ds_model" \
  MAX_THINKING_TOKENS="${DS_MAX_THINKING:-8000}" \
  "${worker[@]}" --permission-mode acceptEdits \
  --allowedTools "${allow[@]}" --disallowedTools "${deny[@]}" \
  --max-turns "${DS_MAX_TURNS:-120}" --output-format json < /dev/null > "$logdir/$name.json" 2> "$logdir/$name.err" || status=$?

# Never lose a worker's progress: whatever the exit reason (done, max turns, API error),
# snapshot uncommitted changes to refs/wip/<name> without touching the branch or worktree.
snapshot_wip() {
  # Ignored files (e.g. the .secrets symlink) are never added by `git add -A`.
  local tmp_index tree snap
  tmp_index=$(mktemp) || return 1
  cp "$(git rev-parse --git-dir)/index" "$tmp_index" 2>/dev/null || true
  GIT_INDEX_FILE="$tmp_index" git add -A >/dev/null 2>&1 || { rm -f "$tmp_index"; return 1; }
  tree=$(GIT_INDEX_FILE="$tmp_index" git write-tree) || { rm -f "$tmp_index"; return 1; }
  rm -f "$tmp_index"
  snap=$(git commit-tree "$tree" -p HEAD -m "wip snapshot of $name (uncommitted worker changes)") || return 1
  git update-ref "refs/wip/$name" "$snap" || return 1
  echo "$name wip-snapshot=refs/wip/$name ($snap)"
}
if [ -n "$(git status --porcelain --untracked-files=all 2>/dev/null)" ]; then
  snapshot_wip || echo "$name wip-snapshot FAILED — snapshot manually before relaunching" >&2
fi
echo "$name exit=$status head=$(git rev-parse --short HEAD)"
exit "$status"
