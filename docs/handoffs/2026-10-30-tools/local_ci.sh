#!/bin/bash
# local_ci.sh <pr-worktree> <sha>
# Reproduces .github/workflows/checks.yml (unit + p1-postgres) and frontend.yml locally.
# unit/frontend run in the PR worktree; p1-postgres runs in the deploy checkout main-baseline
# (project "safebank": make up / test-integration restart that stack and the network test uses 172.29.x).
# Stops at the first failing step. Exit 0 only if every step passes.
set -u
WT=$1; SHA=$2
W=/Users/zhangshijie/Desktop/Project/Fintech2026-wt
DEPLOY=$W/main-baseline
LOG=$W/.ds-logs/local-ci-$(echo $SHA | cut -c1-7); mkdir -p $LOG
step(){ name=$1; shift; printf '%-28s ' "$name"; if ( "$@" ) > "$LOG/$name.log" 2>&1; then echo "OK  $(tail -1 $LOG/$name.log | cut -c1-90)"; else echo "FAIL (see $LOG/$name.log)"; tail -15 "$LOG/$name.log"; echo "LOCAL CI FAILED at $name"; exit 1; fi; }
cd "$WT" || exit 1
[ "$(git rev-parse HEAD)" = "$(git rev-parse $SHA)" ] || { echo "worktree HEAD != $SHA"; exit 1; }
echo "== unit (in $WT)"
step unit-sync uv sync --frozen --group poc --python 3.12
step unit-pytest uv run --group poc pytest -q
step unit-ruff-check uv run ruff check .
step unit-ruff-format uv run ruff format --check .
if git diff --name-only origin/main...HEAD | grep -qE '^frontend/|^\.github/workflows/frontend.yml'; then
  echo "== frontend"
  step fe-npm-ci bash -c 'cd frontend && npm ci'
  step fe-test bash -c 'cd frontend && npm test'
  step fe-test-bff bash -c 'cd frontend && npm run test:bff'
  step fe-build bash -c 'cd frontend && npm run build'
else echo "== frontend: no frontend changes, skipped (same as CI path filter)"; fi
echo "== p1-postgres (in $DEPLOY at $SHA)"
cd "$DEPLOY" && git fetch -q origin && git checkout -q --detach "$SHA" || { echo "cannot checkout $SHA in deploy"; exit 1; }
for t in setup test-postgres-bootstrap up test-gateway-db test-customer-locks test-wave1-state test-shared-runtime test-agent-db test-w1 test-obligations test-w2 test-w3a test-w3b test-w4b1 test-w4b2b test-w4b2c test-w5-sms test-w4c test-e0b test-p1 test-p1-concurrency test-p1-consistency test-integration contracts-check; do
  grep -qE "^$t:" Makefile || { echo "$(printf '%-28s' p1-$t) SKIP (no such target at this commit)"; continue; }
  step p1-$t make $t
done
# extra targets added to checks.yml on this branch are run too
for t in $(grep -oE 'make test-[a-z0-9-]+' .github/workflows/checks.yml | awk '{print $2}' | sort -u); do
  case " setup test-postgres-bootstrap up test-gateway-db test-customer-locks test-wave1-state test-shared-runtime test-agent-db test-w1 test-obligations test-w2 test-w3a test-w3b test-w4b1 test-w4b2b test-w4b2c test-w5-sms test-w4c test-e0b test-p1 test-p1-concurrency test-p1-consistency test-integration " in *" $t "*) ;; *) step p1-$t make $t;; esac
done
echo "LOCAL CI PASSED for $SHA"
