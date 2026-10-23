import asyncio

import pytest

from services.gateway.app.errors import GatewayError
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.utterance.service import predicate_values
from tests.gateway.w1.fixtures import NOW, bank_state


def test_predicates_use_typed_fake_read_contracts_and_snapshot():
    async def run():
        bank = bank_state()
        fake = FakeL3()
        fake.set_snapshot(bank)
        base = fake._minimal_read_result
        anomaly_tx = {
            "id": "tx_a",
            "account_id": "acc_zs",
            "direction": "OUT",
            "kind": "CARD_SPEND",
            "status": "POSTED",
            "amount_cents": 1000000,
            "balance_after_cents": 600000,
            "available_after_cents": 600000,
            "counterparty": "商户",
            "memo": "合成备注",
            "category": "消费",
            "merchant_id": "mer_x",
            "merchant_name": "商户",
            "payee_id": None,
            "card_id": "crd_zs",
            "occurred_at": NOW.isoformat(),
        }

        def response(tool):
            if tool == "detect_subscriptions":
                return {
                    "items": [
                        {
                            "merchant_id": "mer_gym",
                            "merchant_name": "健身",
                            "instruction_id": "si_gym",
                            "billing_id": "bil_gym",
                            "period": "MONTHLY",
                            "typical_amount_cents": 19900,
                            "occurrences": 3,
                            "last_charged_at": NOW.isoformat(),
                            "is_idle": True,
                            "usage_evidence": {"recent_usage_count": 0, "previous_usage_count": 5},
                            "annual_cost_cents": 238800,
                        }
                    ]
                }
            if tool == "detect_anomalies":
                return {
                    "items": [
                        {
                            "transaction": anomaly_tx,
                            "codes": ["ANOMALY_NIGHT_LARGE"],
                            "evidence": {
                                "baseline_count": 3,
                                "mean_cents": 10000,
                                "threshold_cents": 50000,
                                "previous_transaction_id": None,
                                "window_minutes": None,
                            },
                        }
                    ]
                }
            return base(tool)

        fake._minimal_read_result = response
        l3 = L3Client("http://bank", fake.token, transport=fake.transport())
        evidence = []
        result = await predicate_values(
            l3, bank, "cus_zs", "找闲置订阅、稳健型产品、可疑卡", evidence
        )
        assert result == {
            "robust_products": ["prd_a", "prd_b", "prd_c"],
            "idle_subscriptions": ["si_gym"],
            "suspicious_cards": ["crd_zs"],
        }
        assert {record["tool"] for record in evidence} == {
            "detect_subscriptions",
            "detect_anomalies",
        }
        anomaly = next(record for record in evidence if record["tool"] == "detect_anomalies")
        assert anomaly["args"]["from_at"] == "2026-09-01T00:00:00+08:00"
        fake.behaviors["read"] = "timeout"
        with pytest.raises(GatewayError) as exc:
            await predicate_values(l3, bank, "cus_zs", "闲置订阅")
        assert exc.value.status == 503
        await l3.close()

    asyncio.run(run())


def test_robust_products_require_valid_assessment():
    async def run():
        bank = bank_state()
        invalid = bank.model_copy(
            update={
                "customers": {
                    "cus_zs": bank.customers["cus_zs"].model_copy(update={"risk_level": None})
                }
            }
        )
        with pytest.raises(GatewayError) as exc:
            await predicate_values(None, invalid, "cus_zs", "稳健型")
        assert exc.value.code.value == "ASSESSMENT_MISSING"

    asyncio.run(run())
