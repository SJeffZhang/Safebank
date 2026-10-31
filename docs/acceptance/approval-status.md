# Approval pending status boundary (2026-10-31)

G9 intentionally overlays the current approval-row status onto the stored
PlanView envelope (`services/gateway/app/api/approvals.py`, get_approval_panel).
A pending approval therefore arrives as `PENDING`. The mobile approval sheet
uses `PENDING_APPROVAL` as its pending UI state, also shared by preview plans.
The previous getView passthrough left the real pending panel disabled even after
its reading delay and required risk/OTP inputs.

The fix is restricted to frontend/src/mobile/api.ts:getView: exact `PENDING`
is mapped to the existing UI state on a copied view. Other statuses are returned
unchanged. Approval/plan/evaluation/session identity, binding view_hash, expiry,
risk, risk acknowledgement and OTP flags stay unchanged. No request endpoint,
credentials/CSRF behavior, explicit-confirmation handler, backend rule or
execution path is modified. Preview `PENDING_APPROVAL` stays compatible.

New tests exercise the real live request/getView/Approval boundary, replacing
only external fetch. They cover legal yellow and red pending panels, terminal
and unknown values (including lowercase/whitespace), expiry, unchanged binding
fields, reading delay and the absence of automatic confirmation.

## Observed verification

- New13 cases on original code: **2 failed /11 passed**. Both failures are
  toBeEnabled assertions for legal PENDING panels, not setup errors.
- Same13 cases after the minimal boundary fix: **13 passed**.
- `npm test`: **44 files /413 tests passed**, exit0.
- `npm run test:bff`: **16 passed**, no failures/skips, exit0; test doubles only.
- `npm run build`: TypeScript check and Vite production build passed, exit0.
- `git diff --check`: passed. Package manifest and lockfile unchanged.

Development setup: offline npm installation initially lacked cached xmlchars
and failed ENOTCACHED. The coordinator prepared the existing locked packages
once; no dependency versions or lockfile were changed. An initial fake-clock
userEvent timeout was corrected in the test harness with direct DOM input
simulation, then the meaningful two-assertion red run was captured. Two harness-edit
attempts used incorrect relative paths and changed no file; they were corrected
before the reported red/green results. These setup failures are not counted as
banking regressions.

Final test helpers also explicitly assert disabled before the reading delay. The
final13 tests reran against the original API in an isolated temporary source copy:
**2 failed /11 passed**, with the active worktree untouched. Final full frontend
run remained **413 passed /44 files**, and build passed again. Existing React
Router future-flag warnings occur in unrelated legacy tests; the new file has no
warning output.

Final logs: /private/tmp/sbg-approval-status-final-old-red.log,
/private/tmp/sbg-approval-status-final-full-test.log,
/private/tmp/sbg-approval-status-bff.log and
/private/tmp/sbg-approval-status-final-build.log.

This is development-only verification. No browser, real gateway/model/DB,
Docker stack, screenshot or demonstration was run. Independent safety review,
coordinator rebase/integration and exact-final-head whole local CI remain gates.
No deployment, merge or freeze/tag is claimed.
