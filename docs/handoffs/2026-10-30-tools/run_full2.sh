#!/bin/bash
# Run cases in the isolated demo stack. Nonzero driver results are observations,
# not pass/fail assertions; the caller must verify business state independently.
set -u
S=$(cd "$(dirname "$0")" && pwd) || exit 1
W=${LIVE_WORKTREE_ROOT:-/Users/zhangshijie/Desktop/Project/Fintech2026-wt}
export COMPOSE_PROJECT_NAME=safebank-e2e-1029 COMPOSE_FILE=docker-compose.yml:e2e.override.yml GW_PUBLIC=172.30.10.12
TAG=${RUN_TAG:-$(date -u +%Y%m%dT%H%M%SZ)-$$}
L="$W/.ds-logs/full-e2e-$TAG"
mkdir -p "$L" || exit 1
# Never append a new run to an old summary or accept stale per-case traces.
[ ! -e "$L/summary.tsv" ] || { echo "run already exists: $L"; exit 1; }
cd "$W/e2e-deploy" || exit 1
{
  git rev-parse HEAD
  DB=$(docker compose exec -T bank-backend python -c 'import os; from urllib.parse import urlsplit; print(urlsplit(os.environ["BANK_DATABASE_URL"]).path.lstrip("/"))') || exit 1
  [[ "$DB" =~ ^[a-zA-Z0-9_]+$ ]] || exit 1
  echo "database=$DB"
  docker exec safebank-e2e-1029-postgres-bank-1 psql -U postgres -d "$DB" -tAc 'select dataset_revision,sim_now from clock'
} > "$L/manifest.txt" 2> "$L/manifest.err" || { echo "metadata failed"; exit 1; }
cd "$W" || exit 1
while IFS=$'\t' read -r n c t; do
  [ -z "$n" ] && continue
  [[ "$n" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "invalid case id"; exit 1; }
  "$S/snap3.sh" "$c" > "$L/$n.before" 2>&1 < /dev/null || exit 1
  start=$(date +%s)
  # The output path is unique per run; a failed run cannot reuse an old trace.
  out="/tmp/try-agent-$TAG-$n.json"
  TRY_AGENT_OUT="$out" python3 "$S/drive_try_agent.py" e2e-deploy W0-foundation/.secrets/demo_pins "$c" "$t" ok > "$L/$n.log" 2>&1 < /dev/null
  rc=$?
  (cd "$W/e2e-deploy" && docker compose exec -T operator cat "$out") > "$L/$n.trace.json" 2>/dev/null < /dev/null
  dur=$(( $(date +%s) - start ))
  "$S/snap3.sh" "$c" > "$L/$n.after" 2>&1 < /dev/null || exit 1
  ex=$(grep -o "EXIT: [A-Z_]*" "$L/$n.log" | tail -1)
  err=$(python3 "$S/trace_err.py" "$L/$n.trace.json")
  rep=$(grep "^Reply:" "$L/$n.log" | tail -1 | cut -c1-200)
  ch=$(diff "$L/$n.before" "$L/$n.after" | grep '^[<>]' | grep -v 'plan_exec_total\|event_exec_total' | tr '\n' ';' | cut -c1-500)
  printf '%s\t%s\t%s\trc=%s\t%ss\t%s\t%s\t[%s]\t%s\n' "$n" "$c" "$t" "$rc" "$dur" "$ex" "$rep" "$ch" "$err" | tee -a "$L/summary.tsv"
  if [ "$rc" = 75 ] || [ "$rc" = 124 ]; then
    echo "STOPPED rc=$rc; inspect current plan before another live request" | tee "$L/STOPPED"
    exit "$rc"
  fi
  if [ ! -s "$L/$n.trace.json" ] || [ "$rc" -gt 13 ]; then
    echo "driver/trace failure; stopping batch" | tee "$L/STOPPED"
    exit 1
  fi
done < "${CASES:-$S/full_cases2.tsv}"
echo DONE
