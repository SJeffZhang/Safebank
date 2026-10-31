"""Quota/access responses must reach the driver even during answer fallback."""

import httpx
import pytest
from openai import APIStatusError

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.respond import RespondAdapter
from services.agent.app.llm.verify import OutputVerifier
from tests.agent.llm_mock import ScriptedLLM


@pytest.mark.parametrize("status", [402, 403, 429])
@pytest.mark.parametrize("node", ["respond", "verify"])
def test_provider_stop_response_is_not_hidden_by_fallback(status, node):
    scripted = ScriptedLLM(
        [httpx.Response(status, json={"error": {"message": "upstream unavailable"}})]
    )
    llm = make_llm(api_key="test-key", http_client=scripted.client)
    if node == "respond":
        adapter = RespondAdapter(llm)
        state = {"reads": [], "utterances": []}
    else:
        adapter = OutputVerifier(llm)
        state = {
            "reply_draft": "已转账900.00元。",
            "execution": {
                "status": "EXECUTED",
                "steps": [{"result": {"balance_cents": 80000}}],
            },
        }
    with pytest.raises(APIStatusError) as error:
        adapter(state, None)
    assert error.value.status_code == status
    assert len(scripted.requests) == 1
