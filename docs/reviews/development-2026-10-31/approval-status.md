# Task7 independent approval-status safety review

Verdict: **APPROVE** for exact candidate `62c9ea39a3975106967be4fcb41be3a1c6099906`. No blocking findings in the reviewed change. Parent serial integration/rebase and exact-final-head complete local CI remain required; no real-service or demonstration acceptance is claimed.

Base: `c6a1d2ed83ecd06491ee02a8ed3e8fcbd8151f5a`  
Worktree: `/Users/jet/.codex/worktrees/safebank-aa-self-source/WeBank`

## Scope

Read-only review of all three changed files, actual G9 projection, neighboring API request/approval component code and tests, current AGENTS1–3/8 and relevant PRD G9/status/security passages under the coordinator8.7 targeted-task exception. Shared AGENTS/PRD and implementation were not modified. No browser, screenshot, real application/API/model, Docker, database or credential access; no push/merge/tag or helper agent. Only this independent report was written.

## Findings

No actionable blocking finding.

- `frontend/src/mobile/api.ts:79–85` maps only strict, case-sensitive `PENDING` into a copied `PENDING_APPROVAL` UI view. It does not trim/normalize arbitrary values or default unknown states to pending. All other states are returned unchanged. The existing URL encoding, GET request, same-origin credentials, client header and error behavior are untouched.
- The spread copies the complete original panel without replacing plan/approval/evaluation/session identity, binding `view_hash`, expiry, risk, `risk_ack_required` or `otp_required`. No confirmation/execution request is added by the mapping.
- `services/gateway/app/api/approvals.py:294–298` confirms the real G9 envelope is intentionally overlaid with the live approval-row status and expiry. Backend identity cross-checks remain unchanged. Status is explicitly outside the content-hash domain, so the UI alias does not recalculate or weaken the binding hash. No server wire contract or authorization rule is changed.
- `frontend/src/mobile/Approval.tsx:355–364` remains unchanged: busy/reading delay/expiry/pending status/required six OTP digits/explicit risk acknowledgement all gate the confirmation control; actual submission still requires a trusted user event. The unchanged submit path still sends the original `view_hash`, OTP and risk acknowledgement and follows existing agent-resume or form-execute routing.
- New tests exercise the real live `request`/`getView` plus real `Approval`, mocking only fetch. They expose the actual protocol mismatch that older component fixtures (mocked `getView`) did not cover. They retain terminal/unknown/lowercase/whitespace refusal, expired refusal, reading delay, required OTP/risk controls and no automatic POST; binding fields are checked unchanged. No existing assertion or dependency lock was removed/altered.

## Independent verification

1. Exact-candidate targeted new13 plus neighboring Approval5: **18 passed**, exit0,4.03s. Log `/private/tmp/sbg-task7-independent-focused.log`.
2. Independently archived tracked base frontend source into a new retained temporary directory, excluded env files, overlaid only the final new test from the exact candidate and linked installed development dependencies. Confirmed the archived API bytes equal `git show` of the exact base; active-worktree source was not overwritten. Old API with final tests: **2 assertion failures /11 passed**, exit1,0.92s. Both failed legitimate pending-panel `toBeEnabled` assertions (yellow and red after original prerequisites), not harness/setup failures. Scratch `/private/tmp/sbg-task7-review-old-yhrhfcb9`; log `/private/tmp/sbg-task7-independent-old-red.log`.
3. `npm run typecheck`: passed, exit0. Log `/private/tmp/sbg-task7-independent-typecheck.log`.
4. Candidate diff whitespace check passed; final `git status --short` empty and exact HEAD remained `62c9ea39a3975106967be4fcb41be3a1c6099906`.

The author's frontend413/BFF16/build runs are recorded in the component acceptance document; they are not relabeled as independent runs here. No full project CI, real gateway/browser or demonstration was performed by this reviewer.

## Parent gates

Serial rebase onto the current main, preserve upstream changes, update coordinator-owned status/interface docs, run the complete required local workflows on the exact final SHA (including frontend workflow because frontend changed), and only then integrate under the project's existing merge policy. Actual demonstration stays outside the user's current development-only scope.
