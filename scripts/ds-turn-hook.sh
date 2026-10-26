#!/usr/bin/env bash
# PostToolUse hook for DeepSeek workers launched by scripts/ds-worker.sh.
# The model cannot see how many turns it has left, so count tool calls here and,
# once within DS_TURN_WARN of the limit, inject a "commit now" reminder into its context.
# Usage (set by ds-worker.sh): ds-turn-hook.sh <counter-file> <max-turns> <warn-margin>
counter=$1 limit=$2 warn=${3:-5}
cat > /dev/null  # drain the hook's JSON input
n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$counter"
left=$(( limit - n ))
if [ "$left" -le "$warn" ]; then
  msg="[launcher] Tool calls used: $n of $limit (about $left left). Stop exploring now: git add your changed files and git commit a checkpoint, then update the progress file with real command results. Uncommitted work at the limit is only snapshotted, not delivered."
  printf '{"hookSpecificOutput":{"hookEventName":"PostToolUse","additionalContext":"%s"}}\n' "$msg"
fi
exit 0
