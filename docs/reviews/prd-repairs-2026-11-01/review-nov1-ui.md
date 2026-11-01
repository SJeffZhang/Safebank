# November 1 frontend independent review

Initial target: dec35db2448615abb4a07b06135d05d9f1284bbf. Final corrected target: e61b86ddf65ce339c3b8a795e9ee868f35609746. Base: aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1. Reviewed from exact git archive in /private/tmp/sbg-nov1-repairs/review-ui. No product/worktree/WIP modifications, real API/DB/Docker/provider/browser calls.

## Verdict

APPROVE at e61b86ddf65ce339c3b8a795e9ee868f35609746 after the original P2 was corrected and independently rechecked; initial REQUEST_CHANGES evidence remains below.

## Changes and interface boundaries

Checked G21 parent -> G19 -> action-only G3, explicit typed SLOT/new utterance separation, fixed FORM whitelist and dual-month reads; details below.

## Findings

One P2 finding, documented below.

## Verification

Executed55 focused FE tests and4 independent negatives; full FE/BFF/build not repeated after the actionable finding, per coordinator. Original fixture tests remain unchanged.

## Uncovered limits

No real browser/services, current source archive excludes coordinator's later server changes. Interface alignment checked against documented fixed G21 template IDs only.

## Initial source evidence

- Exact four commits only touch Assistant wire/interaction, month selection, tests and acceptance; no BFF or banking write code changed.
- G21 serializer already exposes parent_utterance_ids/expires_at_wall; G19 models require parent membership; action-only G3 may not carry typed clarification_id. New choose matches these contracts and fails before G3 for absent/nonunique parent, bad action ID, failed G19 and invalidated session/unmount markers.
- FORM lookup uses Object.prototype.hasOwnProperty.call against three fixed frontend template IDs, exactly matching coordinator's specified server additions. Routes/copy are fixed; no prefill URL or submission action is constructed. Unknown/__proto__/constructor safely fall back.
- billMonths preserves formal14 primary9/secondary8 using Shanghai simulation and still rejects other source months; Workspace gets primary9 and expands its actual September range. Existing single-month handling is retained.
- Focused independent execution and additional negatives are in progress. Potential duplicate typed-answer behavior is being checked, not yet graded.

## Initial verdict: REQUEST_CHANGES

### P2 — explicit typed SLOTS submissions have no synchronous admission lock

`frontend/src/mobile/Assistant.tsx:230–236` checks React `busy` state, appends a user reply and calls run(), whose own guard is also React busy state. Unlike choose(), this new typed-answer path does not acquire a synchronous ref before starting G3. Two submissions in one React event batch therefore both register typed utterances with the same clarification_id and different request IDs. Independent reproduction yields three message requests (original + two answers) instead of two; this is a request race/error ambiguity, not evidence that L2 permits duplicate fund writes. The server is still expected to reject the later consumed clarification.

Reproduction (exact target archive, production code unchanged):

`cd /private/tmp/sbg-nov1-repairs/review-ui && npm --prefix frontend test -- src/mobile/AssistantIndependentReview.test.tsx`

Four reviewer negatives: **3 passed / 1 failed**. Failing test is `review: rapid explicit typed SLOT answers are admitted once` at line106; log `/private/tmp/sbg-nov1-repairs/review-ui-negatives.log`. It calls fireEvent.click twice inside one act batch. This deliberately exercises same-batch re-entry rather than claiming an ordinary separately flushed browser double-click was observed. Other independent negatives pass: malformed G19 action ID never reaches G3; switching sid away and back invalidates deferred G19; FORM_REQUIRED with candidates creates no G19/typed-SLOTS UI and no prefill URL.

Fix direction: acquire a synchronous per-submission marker before appending/sending the typed answer; release only the matching marker and preserve sid/unmount invalidation. Add coverage for success/failure unlock and cross-sid/unmount. Coordinator has assigned the owner a narrow fix; this initial failure remains archived and will not be replaced by the later retest.

## Initial verification results

- Exact-head focused AssistantClarification/Comparison/Topics/Wire/PlanLifecycle: **55 passed across5 files**. Log `/private/tmp/sbg-nov1-repairs/review-ui-special.log`.
- Independent negatives: **3 passed / 1 failed**, as above.
- No additional full FE/BFF/build run requested after this finding; coordinator will run exact-head final CI after the narrow correction. The implementation author's prior474/16/build claim was read but is not represented as this reviewer's execution.
- No product, worktree or WIP files changed. Only copied dependency files, test-only reviewer fixture in the temporary archive, logs and this report were written. No actual API/DB/Docker/model/browser was used.

## Correction recheck — e61b86ddf65ce339c3b8a795e9ee868f35609746

Reviewed as a separate exact git archive in `/private/tmp/sbg-nov1-repairs/review-ui-e61`; the original dec35 archive, reviewer failing probe and log above remain unchanged.

The narrow source delta acquires `selection.current` synchronously before appending/sending an explicit typed answer, uses the same marker as candidate selection, and releases only its own matching marker in finally. Existing sid/unmount cleanup invalidates that marker and aborts the stream. The previous same-batch reproduction now passes; the added author control also demonstrates deliberate retry after409 once the matching lock releases. Normal typed messages retain `clarification_id:null`; only the explicit SLOTS input attaches the current clarification ID. No FORM, approval or authorization behavior was expanded.

Recheck command in the new archive:

`npm --prefix frontend test -- src/mobile/AssistantIndependentReview.test.tsx src/mobile/AssistantClarification.test.tsx src/mobile/AssistantComparison.test.tsx src/mobile/AssistantTopics.test.tsx src/mobile/AssistantWire.test.tsx src/mobile/PlanLifecycle.test.tsx`

Result: **61 passed across6 files** (57 author checks plus4 unchanged reviewer negatives). `npm --prefix frontend run typecheck` also exits0. Log `/private/tmp/sbg-nov1-repairs/review-ui-e61-special.log`. No new substantive finding in the narrow correction; no full frontend/BFF/build repeat per coordinator's instruction.

### Final disposition

**APPROVE** for the frontend component at exact `e61b86ddf65ce339c3b8a795e9ee868f35609746`, with the original P2 now closed by the independently rerun same test. Initial REQUEST_CHANGES is retained above as historical evidence. This approval does not claim real browser/case38/case14/SQL acceptance or final combined-head CI; those remain coordinator gates. Root's three fixed G21 template values match the frontend whitelist without scope additions.
