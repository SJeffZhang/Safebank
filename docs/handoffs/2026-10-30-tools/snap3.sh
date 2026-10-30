#!/bin/bash
# Full state snapshot for one customer ($1) in the clean e2e environment.
C=$1
docker exec -i safebank-e2e-1029-postgres-bank-1 psql -U postgres -d bank -tA -F' ' -v c="$C" <<'SQL'
select 'acct '||id||' bal='||(credits_posted_cents-debits_posted_cents)||' pend_dr='||debits_pending_cents from accounts where customer_id=:'c' order by id;
select 'card '||c.id||' '||c.status||' single='||c.single_limit_cents||' daily='||c.daily_limit_cents||' controls='||c.controls::text from cards c join accounts a on a.id=c.account_id where a.customer_id=:'c' order by c.id;
select 'payee '||id||' '||name from payees where customer_id=:'c' order by id;
select 'si '||id||' '||kind||' '||status||' amt='||coalesce(amount_cents::text,'-')||' payee='||coalesce(payee_id,'-')||' to='||coalesce(to_account_id,'-')||' anchor='||coalesce(anchor_day::text,'-') from standing_instructions where customer_id=:'c' order by id;
select 'billing '||id||' active='||active from merchant_billing where customer_id=:'c' order by id;
select 'split '||id from split_requests where customer_id=:'c' order by id;
select 'risk '||id||' level='||level from risk_assessments where customer_id=:'c' order by id;
select 'holding '||h.product_id||' units='||h.units||' reserved='||h.reserved_units from holdings h join accounts a on a.id=h.account_id where a.customer_id=:'c' order by h.product_id;
select 'order '||id||' '||type||' '||status||' amt='||amount_cents||' units='||units from fund_orders where customer_id=:'c' order by id;
select 'txns='||count(*) from transactions t join accounts a on a.id=t.account_id where a.customer_id=:'c';
select 'plan_exec_total='||count(*) from plan_executions;
select 'event_exec_total='||count(*) from event_executions;
select 'ledger_balanced='||(sum(debits_posted_cents)=sum(credits_posted_cents) and sum(debits_pending_cents)=sum(credits_pending_cents)) from accounts;
SQL
