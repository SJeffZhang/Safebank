# Business dates and upcoming target display independent review

Exact target:5bb52447ebef0daa7200c908202ab8e34052184f. Base:0f96570. Reviewed from exact git archive at /private/tmp/sbg-nov1-repairs/review-business-dates-5bb. No product/WIP modifications; no real DB/API/provider/browser/Docker.

## Verdict

APPROVE; source checks and independent pure probes completed below.

## Business date restoration and quote math

Check risk.valid_until and product.base_date use Shanghai calendar only; actual timestamp instants remain unchanged and quote growth is not advanced by a day.

## Upcoming target display

Check only current-customer payee or owned target-account names, safe fallback for missing/foreign records, preserved untrusted path and unchanged amounts/reserve.

## Independent verification

508 passed/3 PG skipped in combined run, then11 independent controls passed; PG evidence inspected only.

## Findings and limits

APPROVE; source checks and independent pure probes completed below.

## Source and interface conclusions

The two state-loader changes normalize only the stored calendar fields with astimezone(Asia/Shanghai).date(). Assessment assessed_at and fund confirmation/settlement fields are not rewritten. Non-MMF NAV still comes from the same Decimal business-day function; restoring the correct anchor prevents an extra day, while MMF remains constant. R13 expiry now agrees with the unchanged customer-based R18 effective date.

Upcoming display first reads a payee only if its stored customer_id is the current request's customer. Otherwise an OWN target account also requires matching account ownership before its display_name is used. OTHER/EXTERNAL targets receive fixed non-ID descriptions. Registered current-customer payee labels are allowed even for another customer's internal target; the other customer's actual account name is not used. Existing trust.yaml continues to mark target_display untrusted. No reserve/calculation/money fields or write path changed.

## Executed independent checks

- Author five pure boundary tests + all bankcore + first9 independent probes + PG test skip: **508 passed /3 skipped**, log `/private/tmp/sbg-nov1-repairs/review-business-dates-5bb-pure.log`.
- Independent probe file extended with two foreign internal-account controls: **11/11 passed** total. Checks cover UTC midnight/04:30 expiry restoration with assessed_at instant unchanged, BOND anchor-day1.0000/next-trading-day1.0001/weekend1.0001, MMF constant1.0000, raw SQL rows unchanged, current-vs-foreign payee ownership, owned savings display name, and other-customer account names never leaked. Scheduled amount/reserve remain1000 cents.
- Ruff on changed production and author test files exits0. All tests ran only in the exact archive using synthetic SQL/read boundaries; no DB/provider/browser/API/Docker was invoked.
- Actual PG tests were inspected only: they append a lawful independent product, set UTC session, check Shanghai00:30/12:30 assessment and midnight anchor, and retain the prior actual trade_date persist/load/read test. Root's old2-red/new3-green PG execution is not recast as reviewer execution. Documentation explicitly does not claim the original non-midnight43-seed had quote/money failures.

## Final disposition

**APPROVE** at exact5bb52447ebef0daa7200c908202ab8e34052184f for this business-date/upcoming-display component. No actionable funds/display/source isolation issue found. Root owns true PG, integrated exact-head CI and merge; original formal43 results remain unchanged.
