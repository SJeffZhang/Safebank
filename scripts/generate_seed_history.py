#!/usr/bin/env python3
"""Deterministic generator for 张三's synthetic transaction history.

Rewrites ``services/bank-backend/fixtures/l1_seed_v1.json`` with a concrete,
reproducible history on ``acc_zs_chk`` that the bankcore analytics can detect:

- exactly 3 anomalies on ``crd_zs_1`` (night-large / duplicate / new-merchant-large),
- exactly 4 monthly subscriptions (video/music/cloud/gym) with the gym idle,
- the same 13 existing transactions (verbatim refund memo) and a final
  ``balance_after_cents`` of 600000 on the latest txn (txn_zs_013).

The 13 original transactions and the verbatim refund memo are preserved
byte-for-byte; only ``balance_after_cents`` on every transaction and the
``credits/debits_posted_cents`` on ``acc_zs_chk`` are recomputed so the
checking ledger still balances to 600000.

Run from the repository root::

    python scripts/generate_seed_history.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / "services" / "bank-backend" / "fixtures" / "l1_seed_v1.json"

ACCOUNT = "acc_zs_chk"
CARD = "crd_zs_1"
ORIGINAL_OPENING_CREDIT = 1_087_950  # balance before txn_zs_001
IN_TOTAL = 120_000  # txn_zs_013 refund (the only IN)
FINAL_BALANCE = 600_000

# The 13 transactions that must survive verbatim (ids are the only thing that
# distinguishes them from generated rows, so an old generated file can be
# re-run safely).
ORIGINAL_TXN_IDS = frozenset(f"txn_zs_{i:03d}" for i in range(1, 14))

# --- merchants -------------------------------------------------------------
# New everyday merchants + the three anomaly merchants. ``usage_expected`` is
# set on mer_gym (existing row) so ``detect_subscriptions`` marks it idle.
NEW_MERCHANTS = [
    {"id": "mer_nightbar", "name": "夜色酒吧", "category": "娱乐"},
    {"id": "mer_waimai", "name": "美味外卖", "category": "外卖"},
    {"id": "mer_electronic", "name": "星耀电子", "category": "数码"},
    {"id": "mer_coffee", "name": "晨光咖啡", "category": "餐饮"},
    {"id": "mer_taxi", "name": "快捷出行", "category": "出行"},
    {"id": "mer_grocery", "name": "便民超市", "category": "零售"},
    {"id": "mer_daily", "name": "日用百货", "category": "日用"},
]
NEW_MERCHANT_IDS = frozenset(m["id"] for m in NEW_MERCHANTS)


def _txn(txn_id, occurred_at, amount, counterparty, kind, merchant_id, category):
    """A CARD_SPEND-style OUT transaction on the checking account."""
    return {
        "id": txn_id,
        "account_id": ACCOUNT,
        "occurred_at": occurred_at,
        "direction": "OUT",
        "amount_cents": amount,
        "counterparty": counterparty,
        "kind": kind,
        "payee_id": None,
        "merchant_id": merchant_id,
        "card_id": CARD,
        "category": category,
        "memo": None,
    }


def _mandate(txn_id, occurred_at, amount, counterparty, merchant_id, category):
    """A MANDATE charge (no card_id)."""
    return {
        "id": txn_id,
        "account_id": ACCOUNT,
        "occurred_at": occurred_at,
        "direction": "OUT",
        "amount_cents": amount,
        "counterparty": counterparty,
        "kind": "MANDATE",
        "payee_id": None,
        "merchant_id": merchant_id,
        "card_id": None,
        "category": category,
        "memo": None,
    }


def _anomaly_txns():
    """The 4 designed anomaly CARD_SPEND txns (3 flagged items)."""
    return [
        # NIGHT_LARGE + NEW_MERCHANT_LARGE (02:13, 128000, first-seen merchant)
        _txn(
            "txn_zs_014",
            "2026-09-07T02:13:00+08:00",
            128_000,
            "夜色酒吧",
            "CARD_SPEND",
            "mer_nightbar",
            "娱乐",
        ),
        # DUPLICATE pair: 4 minutes apart, same card/merchant/amount.
        # The *first* one carries no code (not night, < 100000, no prior).
        _txn(
            "txn_zs_015",
            "2026-09-15T12:00:00+08:00",
            26_800,
            "美味外卖",
            "CARD_SPEND",
            "mer_waimai",
            "外卖",
        ),
        _txn(
            "txn_zs_016",
            "2026-09-15T12:04:00+08:00",
            26_800,
            "美味外卖",
            "CARD_SPEND",
            "mer_waimai",
            "外卖",
        ),
        # NEW_MERCHANT_LARGE (first-seen, > 100000, daytime)
        _txn(
            "txn_zs_017",
            "2026-09-22T15:30:00+08:00",
            349_900,
            "星耀电子",
            "CARD_SPEND",
            "mer_electronic",
            "数码",
        ),
    ]


def _mandate_txns():
    """12 MANDATE charges (4 merchants x 3, 28-31 day intervals)."""
    rows = []
    seq = 18
    for (month, day), amount, name, mid, cat in [
        ((7, 18), 3_000, "爱看视频", "mer_video", "视频娱乐"),
        ((8, 18), 3_000, "爱看视频", "mer_video", "视频娱乐"),
        ((9, 18), 3_000, "爱看视频", "mer_video", "视频娱乐"),
        ((7, 20), 1_500, "乐听音乐", "mer_music", "音乐"),
        ((8, 20), 1_500, "乐听音乐", "mer_music", "音乐"),
        ((9, 20), 1_500, "乐听音乐", "mer_music", "音乐"),
        ((8, 1), 2_100, "云盘", "mer_cloud", "存储"),
        ((9, 1), 2_100, "云盘", "mer_cloud", "存储"),
        ((10, 1), 2_100, "云盘", "mer_cloud", "存储"),
        ((7, 25), 19_900, "力健健身", "mer_gym", "健身"),
        ((8, 25), 19_900, "力健健身", "mer_gym", "健身"),
        ((9, 25), 19_900, "力健健身", "mer_gym", "健身"),
    ]:
        rows.append(
            _mandate(
                f"txn_zs_{seq:03d}",
                f"2026-{month:02d}-{day:02d}T06:00:00+08:00",
                amount,
                name,
                mid,
                cat,
            )
        )
        seq += 1
    return rows


def _gym_visits():
    """6 in-store gym visits (CARD_SPEND at mer_gym) in the 60-120 day window."""
    rows = []
    seq = 30
    for day, amount in [
        (19, 3_990),
        (26, 4_200),
        (2, 4_500),
        (9, 3_990),
        (13, 4_800),
        (15, 3_500),
    ]:
        month = 7 if day >= 19 else 8
        rows.append(
            _txn(
                f"txn_zs_{seq:03d}",
                f"2026-{month:02d}-{day:02d}T19:00:00+08:00",
                amount,
                "力健健身",
                "CARD_SPEND",
                "mer_gym",
                "健身",
            )
        )
        seq += 1
    return rows


def _daily_txns():
    """~140 everyday CARD_SPEND rows spread across June (before the anomaly window)."""
    merchants = [
        ("mer_coffee", "晨光咖啡", "餐饮", 1_500, 3_001),
        ("mer_taxi", "快捷出行", "出行", 300, 501),
        ("mer_grocery", "便民超市", "零售", 2_000, 7_001),
        ("mer_daily", "日用百货", "日用", 500, 2_501),
    ]
    rows = []
    for i in range(140):
        mid, name, cat, base, spread = merchants[i % 4]
        day = 1 + i // 5  # June 1 .. 28
        slot = i % 5
        hour = 8 + slot
        minute = (i * 7) % 60
        amount = base + (i * 13) % spread
        rows.append(
            _txn(
                f"txn_zs_{100 + i:03d}",
                f"2026-06-{day:02d}T{hour:02d}:{minute:02d}:00+08:00",
                amount,
                name,
                "CARD_SPEND",
                mid,
                cat,
            )
        )
    return rows


def _enrich_original(transactions):
    """Set explicit card_id + category on the three October CARD_SPEND rows."""
    meta = {
        "txn_zs_010": ("餐饮", CARD),
        "txn_zs_011": ("出行", CARD),
        "txn_zs_012": ("零售", CARD),
    }
    for txn in transactions:
        if txn["id"] in meta:
            category, card_id = meta[txn["id"]]
            txn["category"] = category
            txn["card_id"] = card_id
    return transactions


def main() -> None:
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    # --- merchants ---------------------------------------------------------
    merchants = [m for m in seed["merchants"] if m["id"] not in NEW_MERCHANT_IDS]
    for m in merchants:
        if m["id"] == "mer_gym":
            m["usage_expected"] = True
    merchants.extend(NEW_MERCHANTS)
    seed["merchants"] = merchants

    # --- transactions ------------------------------------------------------
    original = [t for t in seed["transactions"] if t["id"] in ORIGINAL_TXN_IDS]
    assert len(original) == 13, [t["id"] for t in seed["transactions"]]
    _enrich_original(original)

    new_txns = _anomaly_txns() + _mandate_txns() + _gym_visits() + _daily_txns()
    new_out_total = sum(t["amount_cents"] for t in new_txns)

    all_txns = sorted(original + new_txns, key=lambda t: (t["occurred_at"], t["id"]))

    # Recompute the running balance_after chain from the opening credit.
    credits = ORIGINAL_OPENING_CREDIT + new_out_total
    debits = 0
    for txn in all_txns:
        if txn["direction"] == "IN":
            credits += txn["amount_cents"]
        else:
            debits += txn["amount_cents"]
        txn["balance_after_cents"] = credits - debits

    assert credits == 1_207_950 + new_out_total, credits
    assert debits == 607_950 + new_out_total, debits
    assert credits - debits == FINAL_BALANCE, credits - debits
    # The chronologically latest transaction is the refund and closes at 600000.
    assert all_txns[-1]["id"] == "txn_zs_013", all_txns[-1]["id"]
    assert all_txns[-1]["balance_after_cents"] == FINAL_BALANCE

    seed["transactions"] = all_txns

    # --- account totals -----------------------------------------------------
    for account in seed["accounts"]:
        if account["id"] == ACCOUNT:
            account["credits_posted_cents"] = credits
            account["debits_posted_cents"] = debits
            assert credits - debits == account["ledger_balance_cents"] == FINAL_BALANCE

    SEED_PATH.write_text(
        json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {len(all_txns)} transactions "
        f"(new OUT {new_out_total} cents, credits {credits}, debits {debits})"
    )


if __name__ == "__main__":
    main()
