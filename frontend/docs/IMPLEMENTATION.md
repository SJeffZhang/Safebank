# Frontend rebuild · 2026-10-28

User direction: inherit the teammate's Graphite & Champagne design language and exact existing tokens. No FinMate assets or minified code copied. Its repository only contains built files and has no license; reference is conceptual only.

## Components and acceptance

1. Contract client: inputs are PRD §9 snake_case request objects; outputs are ReadEnvelope, Decision, PlanView, PlanStatus and SSE events. Test cents parsing, fragmented SSE and request flow. Fail on leaked service tokens, guessing approval from assistant text, or treating UNKNOWN as success.
2. Approval component: accepts only approval ID, refetches PlanView, shows bound fields/preflight/disclosures/expiry; confirm is disabled for 1 second and requires trusted gesture. OTP bypasses L1. Test stale hash, expiry, OTP and unknown status via contract mocks and browser checks.
3. Six business surfaces and assistant: account/transfer/scheduled/AA, bills/anomalies, cards, subscriptions, wealth/questionnaire/holdings, cross-scene chat, security center. Verify navigation, normal/invalid forms and empty/error states. Preserve original tokens and theme.
4. BFF: cookie-only server session, allowlisted routes, same-origin + custom-header CSRF checks, no token response, bound original assistant token for resume. Test with local fake gateway/agent servers. No real LLM requested during UI verification.

QA inventory: portrait 375×812 App surface on the available browser viewport (separate physical-phone verification remains pending), dark mode, all main pages, transfer amount invalid/insufficient, yellow confirm, red OTP + risk acknowledgement, reject, card status refresh, subscription cancellation, product comparison, questionnaire completeness, assistant error/cancel, security empty/records, keyboard modal focus, malicious free text as text, UNKNOWN no success. Real backend incompleteness must remain visible as error; preview is explicitly labeled.

Legacy `src/pages`, `src/components`, services and tests remain for traceability. New application entry is `src/mobile`; legacy tests do not prove new behavior.

Actual results and unverified boundaries are in [ACCEPTANCE.md](ACCEPTANCE.md).
