#!/bin/bash
# Full backend e2e rerun on the clean environment safebank-e2e-1029.
S=$(dirname "$0"); W=/Users/zhangshijie/Desktop/Project/Fintech2026-wt
export COMPOSE_PROJECT_NAME=safebank-e2e-1029 COMPOSE_FILE=docker-compose.yml:e2e.override.yml GW_PUBLIC=172.30.10.12
L=$W/.ds-logs/full-e2e-${RUN_TAG:-20261030}; mkdir -p $L; cd $W
while IFS=$'\t' read -r n c t; do
  [ -z "$n" ] && continue
  $S/snap3.sh "$c" > $L/$n.before 2>&1 < /dev/null
  start=$(date +%s)
  TRY_AGENT_OUT=/tmp/$n.json python3 $S/drive_try_agent.py e2e-deploy W0-foundation/.secrets/demo_pins "$c" "$t" ok > $L/$n.log 2>&1 < /dev/null
  rc=$?; (cd $W/e2e-deploy && docker compose exec -T operator cat /tmp/$n.json > $L/$n.trace.json 2>/dev/null < /dev/null)
  dur=$(( $(date +%s) - start ))
  $S/snap3.sh "$c" > $L/$n.after 2>&1 < /dev/null
  ex=$(grep -o "EXIT: [A-Z_]*" $L/$n.log | tail -1); err=$(python3 $S/trace_err.py $L/$n.trace.json)
  rep=$(grep "^Reply:" $L/$n.log | tail -1 | cut -c1-200)
  ch=$(diff $L/$n.before $L/$n.after | grep '^[<>]' | grep -v 'plan_exec_total\|event_exec_total' | tr '\n' ';' | cut -c1-500)
  printf '%s\t%s\t%s\trc=%s\t%ss\t%s\t%s\t[%s]\t%s\n' "$n" "$c" "$t" "$rc" "$dur" "$ex" "$rep" "$ch" "$err" | tee -a $L/summary.tsv
done < ${CASES:-$S/full_cases2.tsv}
echo DONE
