#!/usr/bin/env bash
# Follow a headless Claude Code worker's live transcript in readable form.
# Usage: scripts/watch-worker.sh <worker-name>   e.g. scripts/watch-worker.sh W0-foundation
#        scripts/watch-worker.sh                  (lists workers with transcripts)
set -euo pipefail

projects="$HOME/.claude/projects"
if [ $# -eq 0 ]; then
  /bin/ls -d "$projects"/*Fintech2026-wt-* 2>/dev/null | sed 's#.*Fintech2026-wt-##'
  exit 0
fi

dir="$projects/-Users-zhangshijie-Desktop-Project-Fintech2026-wt-$1"
file=$(/bin/ls -t "$dir"/*.jsonl 2>/dev/null | head -1)
[ -n "$file" ] || { echo "no transcript for worker $1 in $dir" >&2; exit 1; }
echo "following $file" >&2

tail -n "${LINES_BACK:-30}" -f "$file" | jq --unbuffered -r '
  select(.type == "assistant" or .type == "user")
  | .message.content as $c
  | if ($c | type) == "string" then empty else $c[] |
      if .type == "text" then "💬 " + (.text | .[0:300])
      elif .type == "tool_use" then
        "🔧 " + .name + " " + ((.input.command // .input.file_path // .input.pattern // "") | tostring | .[0:160])
      elif .type == "tool_result" then
        "   ↳ " + ((.content | if type == "array" then (map(.text? // "") | join(" ")) else tostring end)
                   | gsub("\n"; " ") | .[0:160])
      else empty end
    end'
