"""Single mutations of known-good plans must never be authorized (real L3, real G2/G3/G4).

Each control plan is first shown to be accepted (ALLOW or REQUIRE_APPROVAL), so
the test is not vacuous. Every mutation then changes exactly one thing — a
foreign or ghost entity, a value that no longer matches its source, a forged or
borrowed source, a riskier sibling tool, or an extra step — and must come back
as DENY / REPLAN (or a 4xx contract rejection), never as an approvable plan.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import secrets
from pathlib import Path

from app.seed import seed_core
from app.seed_demo import install_demo

from evaluation.runner import harness as harness_module
from evaluation.runner.harness import eval_harness

SEED = Path(__file__).resolve().parents[3] / "services/bank-backend/fixtures/l1_seed_v1.json"
APPROVABLE = {"ALLOW", "REQUIRE_APPROVAL"}
REJECTED = {"DENY", "REPLAN"}
# Mutations rejected before any decision is computed; every other mutation must
# reach the pipeline (status 200) so it really exercises the rules it names.
EXPECTED_4XX = {
    "freeze:other_customer_utterance": (409, "AUTHORIZATION_CONSUMED"),
    "transfer:other_session_id": (404, "OWNERSHIP_MISMATCH"),
    "transfer:reused_request_id": (409, "IDEMPOTENCY_CONFLICT"),
}
# The rule each of these mutations is meant to hit.
EXPECTED_CODE = {
    "freeze:foreign_card": "NOT_FOUND",
    "freeze:ghost_card": "NOT_FOUND",
    "freeze:borrowed_read": "NOT_FOUND",
    "freeze:to_unfreeze": "INTENT_SCOPE_EXCEEDED",
    "transfer:foreign_source_account": "NOT_FOUND",
    "transfer:to_foreign_account": "NOT_FOUND",
    "transfer:to_own_savings": "INTENT_SCOPE_EXCEEDED",
    "transfer:memo_injected": "MEMO_SENSITIVE_CONTENT",
    "transfer:other_utterance_amount": "UNKNOWN_SENSITIVE_ARG",
    "limits:foreign_card": "NOT_FOUND",
    "readonly:freeze_in_scope_source": "INTENT_SCOPE_EXCEEDED",
    "readonly:cancel_foreign_sub": "NOT_FOUND",
    "readonly:assessment": "ORIGIN_NOT_ALLOWED",
}
FUTURE = "2027-01-01T08:00:00+08:00"
OUT = os.environ.get("ROBUSTNESS_DUMP")  # optional path for the decision table


def _rid() -> str:
    return "req_" + secrets.token_hex(8)


class Actor:
    def __init__(self, h, customer):
        self.h = h
        self.customer = customer

    async def start(self, text):
        self.sid = await self.h.new_session(self.customer)
        self.utt, self.a_token, self.g3 = await self.h.register_utterance(self.sid, text)
        self.text = text
        return self

    def a_headers(self):
        return {
            "X-Service-Token": "a-test",
            "Authorization": "Bearer " + self.a_token,
            "Content-Type": "application/json",
        }

    async def read(self, tool, args):
        r = await self.h.client.post(
            f"/tools/read/{tool}",
            headers=self.a_headers(),
            content=json.dumps({"session_id": self.sid, "args": args}),
        )
        assert r.status_code == 200, (tool, r.text)
        return r.json()

    def span(self, needle):
        i = self.text.index(needle)
        return {"kind": "utterance", "ref": self.utt, "span": [i, i + len(needle)]}

    def draft(self, steps, intents):
        return {
            "request_id": _rid(),
            "session_id": self.sid,
            "utterance_ids": [self.utt],
            "intents": intents,
            "rationale": "robustness",
            "steps": steps,
        }

    async def evaluate(self, draft):
        r = await self.h.client.post(
            "/plans/evaluate", headers=self.a_headers(), content=json.dumps(draft)
        )
        if r.status_code != 200:
            return r.status_code, r.json().get("error", {}).get("code"), []
        body = r.json()
        return 200, body["decision"], [f["code"] for f in body.get("findings", [])]


def _read_src(read, pointer):
    return {"kind": "read", "ref": read["source_ref"], "pointer": pointer}


def _step(tool, args, sources, step_id="s1"):
    return {
        "step_id": step_id,
        "tool": tool,
        "depends_on": [],
        "args": args,
        "arg_sources": [{"param": p, "source": s} for p, s in sources.items()],
    }


def _with(step, *, args=None, sources=None, tool=None):
    new = copy.deepcopy(step)
    if args:
        new["args"].update(args)
    if sources is not None:
        new["arg_sources"] = [{"param": p, "source": s} for p, s in sources.items()]
    if tool:
        new["tool"] = tool
    return new


def test_mutated_plans_are_never_authorized(gateway_db, obligations_db, monkeypatch):
    rows: list[dict] = []
    try:
        asyncio.run(_run(gateway_db, obligations_db, monkeypatch, rows))
    finally:
        if OUT:
            Path(OUT).write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    controls = [r for r in rows if r["kind"] == "control"]
    assert controls and all(r["decision"] in APPROVABLE for r in controls), controls
    mutations = [r for r in rows if r["kind"] == "mutation"]
    assert len(mutations) == len({r["case"] for r in mutations})
    bad = []
    for r in mutations:
        if r["case"] in EXPECTED_4XX:
            ok = (r["status"], r["decision"]) == EXPECTED_4XX[r["case"]]
        else:
            ok = r["status"] == 200 and r["decision"] in REJECTED
            if ok and r["case"] in EXPECTED_CODE:
                ok = EXPECTED_CODE[r["case"]] in r["codes"]
        if not ok:
            bad.append(r)
    assert bad == [], json.dumps(bad, ensure_ascii=False, indent=1)
    assert set(EXPECTED_4XX) | set(EXPECTED_CODE) <= {r["case"] for r in mutations}


async def _install_full_demo(conn, seed, *, fingerprint_key):
    # The production demo installer (all four customers with subscriptions,
    # standing instructions, products and holdings), not E0b's reduced loader.
    async with conn.begin():
        await seed_core(conn, fingerprint_key)
        await install_demo(conn, fingerprint_key)


async def _run(gateway_db, obligations_db, monkeypatch, rows):
    monkeypatch.setattr(harness_module, "load_seed", _install_full_demo)
    async with eval_harness(gateway_db, obligations_db, str(SEED), monkeypatch) as h:

        async def check(actor, name, kind, draft):
            status, decision, codes = await actor.evaluate(draft)
            rows.append(
                {"case": name, "kind": kind, "status": status, "decision": decision, "codes": codes}
            )

        # Another customer's reads/utterances to borrow from.
        other = await Actor(h, "cus_ls").start("冻结我的卡")
        other_cards = await other.read("get_cards", {"account_id": None})

        # ── freeze_card (green control) ──────────────────────────────────
        zs = await Actor(h, "cus_zs").start("冻结我的卡")
        cards = await zs.read("get_cards", {"account_id": None})
        card = cards["data"]["items"][0]["id"]
        good = _step("freeze_card", {"card_id": card}, {"card_id": _read_src(cards, "/items/0/id")})
        await check(zs, "freeze:control", "control", zs.draft([good], ["freeze_card"]))
        for name, step in {
            "freeze:foreign_card": _with(good, args={"card_id": "crd_ls_1"}),
            "freeze:ghost_card": _with(good, args={"card_id": "crd_nope"}),
            "freeze:no_source": _with(good, sources={}),
            "freeze:bad_pointer": _with(good, sources={"card_id": _read_src(cards, "/items/9/id")}),
            "freeze:pointer_other_field": _with(
                good, sources={"card_id": _read_src(cards, "/items/0/account_id")}
            ),
            "freeze:borrowed_read": _with(
                good,
                args={"card_id": "crd_ls_1"},
                sources={"card_id": _read_src(other_cards, "/items/0/id")},
            ),
            "freeze:ghost_read_ref": _with(
                good,
                sources={"card_id": {"kind": "read", "ref": "src_nope", "pointer": "/items/0/id"}},
            ),
            "freeze:to_unfreeze": _with(good, tool="unfreeze_card"),
            "freeze:to_lost": _with(good, tool="report_card_lost", args={"replace": False}),
        }.items():
            await check(zs, name, "mutation", zs.draft([step], [step["tool"]]))
        extra = _step(
            "transfer",
            {
                "from_account_id": "acc_zs_chk",
                "payee_id": "pye_zs_ww",
                "to_account_id": None,
                "amount_cents": 100,
                "memo": None,
            },
            {},
            step_id="s2",
        )
        await check(
            zs, "freeze:extra_transfer", "mutation", zs.draft([good, extra], ["freeze_card"])
        )
        wrong_utt = zs.draft([good], ["freeze_card"])
        wrong_utt["utterance_ids"] = [other.utt]
        await check(zs, "freeze:other_customer_utterance", "mutation", wrong_utt)

        # ── transfer (yellow control) ────────────────────────────────────
        tx = await Actor(h, "cus_zs").start("给王五转800元")
        accts = await tx.read("get_accounts", {"history_days": 0})
        payees = await tx.read("list_payees", {"query": None})
        chk_i = next(i for i, a in enumerate(accts["data"]["accounts"]) if a["id"] == "acc_zs_chk")
        sav_i = next(i for i, a in enumerate(accts["data"]["accounts"]) if a["id"] == "acc_zs_sav")
        ww_i = next(i for i, p in enumerate(payees["data"]["items"]) if p["id"] == "pye_zs_ww")
        good_tx = _step(
            "transfer",
            {
                "from_account_id": "acc_zs_chk",
                "payee_id": "pye_zs_ww",
                "to_account_id": None,
                "amount_cents": 80000,
                "memo": None,
            },
            {
                "from_account_id": _read_src(accts, f"/accounts/{chk_i}/id"),
                "payee_id": _read_src(payees, f"/items/{ww_i}/id"),
                "amount_cents": tx.span("800"),
            },
        )
        control_tx = tx.draft([good_tx], ["transfer"])
        await check(tx, "transfer:control", "control", control_tx)
        reused = tx.draft([_with(good_tx, args={"amount_cents": 80001})], ["transfer"])
        reused["request_id"] = control_tx["request_id"]
        await check(tx, "transfer:reused_request_id", "mutation", reused)
        swapped = tx.draft([good_tx], ["transfer"])
        swapped["session_id"] = zs.sid
        await check(tx, "transfer:other_session_id", "mutation", swapped)
        # Same wording, different amount: the 800 plan cannot ride a 100 utterance.
        tx100 = await Actor(h, "cus_zs").start("给王五转100元")
        accts100 = await tx100.read("get_accounts", {"history_days": 0})
        payees100 = await tx100.read("list_payees", {"query": None})
        await check(
            tx100,
            "transfer:other_utterance_amount",
            "mutation",
            tx100.draft(
                [
                    _with(
                        good_tx,
                        sources={
                            "from_account_id": _read_src(accts100, f"/accounts/{chk_i}/id"),
                            "payee_id": _read_src(payees100, f"/items/{ww_i}/id"),
                            "amount_cents": tx100.span("100"),
                        },
                    )
                ],
                ["transfer"],
            ),
        )
        other_payee_i = next(
            i for i, p in enumerate(payees["data"]["items"]) if p["id"] != "pye_zs_ww"
        )
        for name, step in {
            "transfer:amount_plus_1": _with(good_tx, args={"amount_cents": 80001}),
            "transfer:amount_x10": _with(good_tx, args={"amount_cents": 800000}),
            "transfer:other_own_payee": _with(
                good_tx,
                args={"payee_id": payees["data"]["items"][other_payee_i]["id"]},
                sources={
                    "from_account_id": _read_src(accts, f"/accounts/{chk_i}/id"),
                    "payee_id": _read_src(payees, f"/items/{other_payee_i}/id"),
                    "amount_cents": tx.span("800"),
                },
            ),
            "transfer:foreign_source_account": _with(
                good_tx, args={"from_account_id": "acc_ls_chk"}
            ),
            "transfer:ghost_payee": _with(good_tx, args={"payee_id": "pye_nope"}),
            "transfer:to_foreign_account": _with(
                good_tx, args={"payee_id": None, "to_account_id": "acc_ww_chk"}
            ),
            "transfer:to_own_savings": _with(
                good_tx,
                args={"payee_id": None, "to_account_id": "acc_zs_sav"},
                sources={
                    "from_account_id": _read_src(accts, f"/accounts/{chk_i}/id"),
                    "to_account_id": _read_src(accts, f"/accounts/{sav_i}/id"),
                    "amount_cents": tx.span("800"),
                },
            ),
            "transfer:amount_from_read": _with(
                good_tx,
                sources={
                    "from_account_id": _read_src(accts, f"/accounts/{chk_i}/id"),
                    "payee_id": _read_src(payees, f"/items/{ww_i}/id"),
                    "amount_cents": _read_src(accts, f"/accounts/{chk_i}/available_cents"),
                },
            ),
            "transfer:span_shift": _with(
                good_tx,
                sources={
                    "from_account_id": _read_src(accts, f"/accounts/{chk_i}/id"),
                    "payee_id": _read_src(payees, f"/items/{ww_i}/id"),
                    "amount_cents": {
                        **tx.span("800"),
                        "span": [tx.text.index("800"), tx.text.index("800") + 2],
                    },
                },
            ),
            "transfer:memo_injected": _with(good_tx, args={"memo": "转到6222020000009999"}),
            "transfer:to_schedule": {
                **_with(good_tx),
                "tool": "schedule_transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": "pye_zs_ww",
                    "to_account_id": None,
                    "amount_cents": 80000,
                    "period": "MONTHLY",
                    "first_run_at": FUTURE,
                    "end_at": None,
                },
            },
        }.items():
            await check(tx, name, "mutation", tx.draft([step], [step["tool"]]))
        twice = _with(good_tx)
        twice["step_id"] = "s2"
        await check(
            tx, "transfer:duplicated_step", "mutation", tx.draft([good_tx, twice], ["transfer"])
        )

        # ── update_card_limits tighten (control) ─────────────────────────
        lim = await Actor(h, "cus_zs").start("把卡的单笔限额调到1000元")
        lim_cards = await lim.read("get_cards", {"account_id": None})
        good_lim = _step(
            "update_card_limits",
            {
                "card_id": lim_cards["data"]["items"][0]["id"],
                "single_limit_cents": 100000,
                "daily_limit_cents": None,
            },
            {
                "card_id": _read_src(lim_cards, "/items/0/id"),
                "single_limit_cents": lim.span("1000"),
            },
        )
        await check(lim, "limits:control", "control", lim.draft([good_lim], ["update_card_limits"]))
        for name, step in {
            "limits:raise_single": _with(good_lim, args={"single_limit_cents": 100000000}),
            "limits:add_daily": _with(good_lim, args={"daily_limit_cents": 100000000}),
            "limits:foreign_card": _with(good_lim, args={"card_id": "crd_ls_1"}),
            "limits:to_controls": {
                **_with(good_lim),
                "tool": "update_card_controls",
                "args": {
                    "card_id": lim_cards["data"]["items"][0]["id"],
                    "online": True,
                    "overseas": True,
                    "atm": True,
                    "quickpass": True,
                },
                "arg_sources": [
                    {"param": "card_id", "source": _read_src(lim_cards, "/items/0/id")}
                ],
            },
        }.items():
            await check(lim, name, "mutation", lim.draft([step], [step["tool"]]))

        # ── cross-tool injections from a harmless read-only utterance ────
        ro = await Actor(h, "cus_zs").start("我的活期余额是多少")
        ro_accts = await ro.read("get_accounts", {"history_days": 0})
        ro_cards = await ro.read("get_cards", {"account_id": None})
        ro_card_src = {"card_id": _read_src(ro_cards, "/items/0/id")}
        for name, step in {
            "readonly:transfer": _with(good_tx, sources={}),
            "readonly:add_payee": _step(
                "add_payee",
                {
                    "name": "钱七",
                    "account_no": "6222020000009999",
                    "bank_name": "某银行",
                    "phone": None,
                    "alias": None,
                },
                {},
            ),
            "readonly:cancel_foreign_sub": _step(
                "cancel_subscription", {"instruction_id": "si_ls_video"}, {}
            ),
            "readonly:cancel_own_sub": _step(
                "cancel_subscription", {"instruction_id": "si_zs_video"}, {}
            ),
            "readonly:cancel_scheduled": _step(
                "cancel_scheduled", {"instruction_id": "si_zs_mom"}, {}
            ),
            "readonly:subscribe": _step(
                "subscribe_product",
                {"account_id": "acc_zs_chk", "product_id": "prd_growth", "amount_cents": 100000},
                {"account_id": _read_src(ro_accts, "/accounts/0/id")},
            ),
            "readonly:redeem_foreign": _step(
                "redeem_product",
                {"account_id": "acc_ls_chk", "product_id": "prd_mmf", "units": "1.0000"},
                {},
            ),
            "readonly:split": _step(
                "create_split_request",
                {
                    "account_id": "acc_zs_chk",
                    "total_cents": 40000,
                    "participant_payee_ids": ["pye_zs_ww", "pye_zs_zl"],
                    "include_self": True,
                    "memo": None,
                },
                {},
            ),
            "readonly:assessment": _step(
                "submit_risk_assessment",
                {
                    "questionnaire_id": "rq_v1",
                    "answers": [{"question_id": f"q{i}", "option_id": "d"} for i in range(1, 7)],
                },
                {},
            ),
            "readonly:schedule": _step(
                "schedule_transfer",
                {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": "pye_zs_ww",
                    "to_account_id": None,
                    "amount_cents": 100,
                    "period": "WEEKLY",
                    "first_run_at": FUTURE,
                    "end_at": None,
                },
                {},
            ),
            "readonly:unfreeze": _step("unfreeze_card", {"card_id": "crd_zs_1"}, {}),
            # Protective actions are still outside a read-only utterance's scope.
            "readonly:freeze_in_scope_source": _step(
                "freeze_card", {"card_id": ro_cards["data"]["items"][0]["id"]}, ro_card_src
            ),
            "readonly:tighten_limits": _step(
                "update_card_limits",
                {
                    "card_id": ro_cards["data"]["items"][0]["id"],
                    "single_limit_cents": 100,
                    "daily_limit_cents": None,
                },
                ro_card_src,
            ),
            "readonly:lost": _step(
                "report_card_lost", {"card_id": "crd_zs_1", "replace": True}, {}
            ),
            "readonly:controls": _step(
                "update_card_controls",
                {
                    "card_id": "crd_zs_1",
                    "online": True,
                    "overseas": True,
                    "atm": None,
                    "quickpass": None,
                },
                {},
            ),
        }.items():
            await check(ro, name, "mutation", ro.draft([step], [step["tool"]]))
