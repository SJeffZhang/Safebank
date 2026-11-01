"""Shape-correct current G20 product selection used by pure X3 regressions."""

from services.agent.app.gateway.adapters import gateway_utterance_to_ref
from services.agent.app.gateway.wire import GatewayUtterance
from services.agent.app.graph.state import Understanding
from tests.agent.test_cross_scene_gather import state


def selected_state():
    # Exact G20 shape after G19 CLARIFICATION + G3 action registration.
    selected = GatewayUtterance.model_validate(
        {
            "id": "utt_selected",
            "kind": "action",
            "text": None,
            "scope": {
                "action_kinds": ["FUND_SUBSCRIBE"],
                "from_account_id": None,
                "transfers": [],
                "period": None,
                "channel": None,
                "fields": {
                    "account_id": "acc_zs_chk",
                    "product_id": "prd_a",
                    "derived_template": "available_less_7d_charges",
                },
            },
            "parse_status": "SCOPED",
            "echoes_untrusted": False,
            "parent_ids": ["utt_original_x3"],
            "clarification_id": None,
            "created_at_wall": "2026-10-31T00:00:00Z",
            "expires_at_wall": "2026-10-31T00:05:00Z",
        }
    )
    current = state("")
    current.update(
        utterances=[gateway_utterance_to_ref(selected)],
        parent_ids=selected.parent_ids,
        clarification=None,
        clarification_id=None,
        hints=[
            {
                "id": selected.id,
                "kind": selected.kind,
                "scope": selected.scope.model_dump(mode="json"),
            }
        ],
        understanding=Understanding(intents=["subscribe_product"], needs_write=True),
    )
    return current
