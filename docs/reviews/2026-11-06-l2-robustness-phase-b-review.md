# Phase B review: commit 3ec074e (L2 plan mutation matrix + BFF error text)

Reviewer: independent read-only Claude subagent, 2026-11-07. Scope: `git show 3ec074e` on
`feat/l2-robustness-matrix`. No DB suites run, no LLM / DeepSeek calls, no files changed except this report.

**Verdict: APPROVE.** None of the findings below is P0 or P1. No production gateway or bank code
changed, so no L2 rule was loosened. All findings are about test strength, accuracy of the docs,
and one wording issue in the user-facing text.

## Findings

### P2-1 The assertion lets any 4xx through, so a regression could make the matrix pass without testing anything
`tests/gateway/robustness/test_l2_plan_mutations.py:118-123`: a mutation fails the test only if its decision is
ALLOW/REQUIRE_APPROVAL or its status is >=500. Any 401, 409 or 429 counts as "rejected".
Scenario: a future change makes G4 mark the utterance CLAIMED or CONSUMED when it accepts the control plan.
That change could come from the plan supersede path or the claims logic. From then on, every mutation that
reuses that utterance gets `409 AUTHORIZATION_CONSUMED` at `utterance/loader.py:62-63`, or
`UTTERANCE_EXPIRED` / `RATE_LIMITED`. The test stays green but no longer exercises the provenance, intent or
ownership rules. The controls are checked before the mutations, so they would not catch this.
Fix: require `status == 200` and a decision in {DENY, REPLAN} for every mutation, except an explicit allowlist.
Today the allowlist is just `freeze:other_customer_utterance -> 409 AUTHORIZATION_CONSUMED`. Better still, pin
the expected finding code for each case, as in the §8.1 table.

### P2-2 Several mutations are rejected for a missing source, not for the property they claim to test
Every draft passes `PlanDraft` validation. I confirmed this offline with a fake harness, and the script is
described under "Verified fine". So no case is rejected because of a typo. Some cases are still not
single-variable:
- `transfer:to_foreign_account` (l.235) and `transfer:to_own_savings` (l.238) set `to_account_id` with no
  source. The rejection is most likely UNKNOWN_SENSITIVE_ARG, so neither the foreign-ownership check nor the
  wrong-target intent check is exercised. Give `to_account_id` a valid read source to fix this: the savings row
  from `accts`, or a borrowed read from cus_ls's session for the foreign account.
- `transfer:to_schedule` (l.261) and `readonly:schedule` (l.373) add `period` / `first_run_at` with no source.
  Both also use the hard-coded `first_run_at` `2026-11-01T08:00:00+08:00`. That is already in the past against
  the gateway's wall clock (the harness clock is real `now`), though still in the future against L3's sim_now
  of 2026-10-15. Either issue alone could be the reason for the rejection. Compute a future time and give it an
  utterance span; otherwise relabel these cases as "riskier tool with unsourced args".
- `limits:to_controls` (l.302): the four flags have no source, so it has the same ambiguity.
- `freeze:other_customer_utterance` (l.190-192) is caught by the generic check that the id must appear in the
  JWT's `utts` (`api/plans.py:252-254`, `loader.py:39-40`). Any id not in the token gives the same 409, so the
  case says nothing specific about crossing customers. Add one more case that is not covered by that check: a
  second utterance from the same customer and session that is missing from the JWT. Better, use a session swap
  (next section) to reach the sid and ownership checks.

### P2-3 Important mutations are missing
- **Protective tools from a read-only utterance.** The `readonly:*` block covers 12 of the 14 write tools.
  `freeze_card` and `update_card_limits` (tightening) are missing. These are the most interesting ones: PRD §7.6
  says a protective action outside the authorized scope must still be REPLAN. Add `readonly:freeze` and
  `readonly:tighten_limit`, each with a valid `get_cards` read source.
- **Cross-session swap.** Post a draft with `session_id` set to another session of the same customer, using the
  current A token. The expected result is 404 OWNERSHIP_MISMATCH (`plans.py:245-246`). Then repeat with the other
  session's token but this session's utterance.
- **Replay of another plan's request_id.** Reuse the control's `request_id` with a mutated body. The expected
  result is 409 IDEMPOTENCY_CONFLICT, and it must not replay the earlier approvable decision. Every case in the
  test currently gets a fresh `_rid()`, so the idempotency path is never exercised.
- **Step dependency and source tricks.** Make `amount_cents` a `{"kind":"step", ...}` source that points at an
  earlier step's output. Try `depends_on` cycles that pass the contract but are semantically odd. Try a `derived`
  source whose template name or inputs come from untrusted reads. Try `hint` / `action` sources with a forged
  `ref` or a hint from another session.
- **Utterance that is valid but has another intent.** Register "给王五转100元" and submit the 800 plan. This is a
  cleaner single-variable amount check than `amount_plus_1`.

### P2-4 Counts in the docs are wrong
The commit message and `docs/reviews/2026-11-06-l2-robustness.md` §8.1 both say 39 mutations. Running the
matrix through a fake harness gives **40** (11 freeze + 12 transfer + 4 limits + 13 readonly). §8.1 also says the
read-only utterance smuggles "13 of the 14 write tools". It is really 13 cases over **12** distinct tools:
`cancel_subscription` appears twice, and `freeze_card` and `update_card_limits` are missing. Correct both numbers
in the docs and the AGENTS log.

### P2-5 BFF: a wrong PIN now shows "登录已失效" ("login expired")
`frontend/server/bff.mjs:47`. A wrong PIN at login produces `GatewayError(UNAUTHENTICATED, 401)`
(`services/gateway/app/api/auth.py:48-49`) with message = code. The BFF now maps that to "登录已失效，请重新登录"
("login expired, please log in again"). `BankSenseApp.tsx:224` shows it on the login screen. That is misleading,
because the user never had a session. Give `/auth/login` its own 401 text in the login branch, such as
"PIN 不正确" ("incorrect PIN"). Leave RATE_LIMITED as it is.

## Verified fine
- **No L2 loosening.** The commit touches only `tests/`, `frontend/server/`, `Makefile`, docs and AGENTS. There
  are no changes under `services/` or `packages/`.
- **Contract validity of all drafts.** I replaced `eval_harness` with a fake that returns synthetic reads and
  validated every captured draft with `PlanDraft.model_validate`. That includes the per-tool `STEP_MODELS` arg
  validation. The 3 controls and all 40 mutations are contract-valid, so none of them is rejected for a typo.
  Script: `/private/tmp/claude-501/-Users-zhangshijie-Desktop-Project-Fintech2026/dc8e13e0-0835-474c-84ec-dd1f0d88f16d/scratchpad/validate_mut.py`.
- **Controls.** All three use real read sources for entities and exact utterance spans for user values, so they do
  not pass by accident. The test asserts that the controls are non-empty and all approvable (l.116-117), which
  guards against a run where nothing is tested.
- **`borrowed_read`.** In the seed, cus_ls owns exactly one card, `crd_ls_1`. So `other_cards` `/items/0/id`
  really equals the arg, and the case does test "value matches its source, but the source is another
  customer's read".
- **Monkeypatch.** `_serve_l3` looks up the module global `load_seed` at call time and calls
  `load_seed(conn, seed, fingerprint_key=...)` (`evaluation/runner/harness.py:113`). The replacement
  `_install_full_demo(conn, seed, *, fingerprint_key)` has a compatible signature. It ignores `seed`, which is
  fine because `install_demo` reads the same `l1_seed_v1.json`. It runs inside `conn.begin()` after `_truncate`,
  so `seed_core` sees an empty DB. The patch is function-scoped through pytest's `monkeypatch`, so nothing leaks
  to other tests. The gateway PIN hashes come from the same seed's customers, which keeps logins consistent.
- **Assertion pieces.** `APPROVABLE` covers every authorizing decision, since the only decisions are ALLOW,
  REQUIRE_APPROVAL, REPLAN and DENY. The `status >= 500` check catches crashes. The weakness is only the 4xx
  side (P2-1).
- **Lint and format.** `ruff check` and `ruff format --check` on the new test file both pass.
- **Makefile / CI.** `OBLIGATIONS_DB_TESTS=1` is required for the `obligations_db` fixture; without it the fixture
  skips. The target is already in the p1-postgres job (`checks.yml:28`). In the unit job the test skips cleanly.
- **BFF behavior.**
  - Nothing in the frontend branches on the message text. Branching happens only on `code`, for example
    `e.code === "CONSOLE_LOGIN_REQUIRED"` and `APPROVAL_EXPIRED`, and `code` and `details` are unchanged.
  - STATE_CHANGED keeps its `details.decision`, and retry hints stay in `details`.
  - Fallback messages for JSON-parse failures already contain CJK, so they are kept.
  - Unknown codes, including `UPSTREAM_ERROR`, get a generic Chinese message.
  - `consoleCall` uses the same mapping. The only effect is that its old default "演示服务暂不可用"
    ("demo service unavailable") becomes the generic text or the mapped text. That is acceptable.
- **CJK regex.** `/[一-鿿]/` in `bff.mjs` is the correct basic CJK Unified Ideographs block. The test's
  literal `/[一-鿿]/` is the same range (U+4E00 to U+9FFF).
- **No new exposure.** Before this change every upstream message passed through verbatim. Now only messages that
  contain CJK pass, which is a strict subset. Gateway messages are set by gateway code (`message or code.value`),
  and React renders them as text. Nothing is newly hidden that matters for security either: the dropped English
  text was developer detail, and `code` and `details` still carry the diagnostic information.
- **BFF tests.** `node --test server/bff.test.mjs`: 17 passed, 0 failed.

## Not run
- The DB-backed matrix itself, per the instructions. I relied on the coordinator's report that the full local CI
  on 3ec074e passed, and the actual decision codes for each mutation were not re-observed by me.
