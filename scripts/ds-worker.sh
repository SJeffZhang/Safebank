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
extra=(); [ -n "$resume" ] && extra=(--resume "$resume")

cd "$wt"
# Clear inherited Anthropic env so the CLI uses ~/.claude/settings.json (DeepSeek), not the desktop app's account.
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY -u ANTHROPIC_MODEL \
  -u ANTHROPIC_DEFAULT_HAIKU_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL -u ANTHROPIC_DEFAULT_OPUS_MODEL \
  -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p "$(cat "$prompt")" "${extra[@]}" --permission-mode acceptEdits \
  --allowedTools "${allow[@]}" --disallowedTools "${deny[@]}" \
  --max-turns 400 --output-format json < /dev/null > "$logdir/$name.json" 2> "$logdir/$name.err"
echo "$name exit=$?"
