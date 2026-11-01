# November 1 signed percentage extraction

Scope: only verify.py percentage pattern/extraction, new test_percentage_signs.py and this record. Root owns other verify logic; no amount parsing, source fact construction, absolute-value allowance, authorization, provider budget or model change.

Evidence: a valid deterministic October report with total55050, previous644800 and mom_change_bp=-9146 emitted-91.46%, but old extraction discarded the sign, compared+91.46 to signed facts and replaced the correct report with GENERIC_ERROR. This is independently reproducible; it is not proof of the original formal12 fallback trigger.

TDD:17 new cases against old code gave11 failed/6 positive controls. Signed extraction now keeps ASCII+/-, Unicode mathematical minus and fullwidth+/-, including its raw span, and normalizes only these signs before numeric conversion. The pattern does not backtrack into the unsigned tail of an adjacent malformed signed/decimal token. Source basis-point facts remain signed; an opposite-sign or unsigned absolute-value claim still fails. Adjacent yuan values remain integer cents. The existing full fixed report survives unchanged verification without invoking a model.

Verification:134 affected verifier/bill/query tests passed; full agent977passed/39skipped; full default4572passed/1121skipped in36.60s; ruff check/format pass. No live provider/API/DB/Docker/browser, no changed formal results. Chinese directional words are not converted into an absolute-value exception; that would require separate semantic proof and is intentionally outside this narrow correction. Root owns exact integrated-head CI and final review.
