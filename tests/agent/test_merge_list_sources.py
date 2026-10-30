"""Per-name utterance sources of one list param collapse into one covering span."""

from services.agent.app.llm.plan import _merge_list_sources


def _utt(start, end, ref="utt_a"):
    return {"kind": "utterance", "ref": ref, "span": [start, end]}


def _plan(sources, ids=("pye_lin", "pye_zhou", "pye_chen")):
    return {
        "steps": [
            {
                "step_id": "s1",
                "tool": "create_split_request",
                "args": {"account_id": "acc_zs_chk", "participant_payee_ids": list(ids)},
                "arg_sources": sources,
            }
        ]
    }


def test_same_utterance_sources_merge_to_covering_span():
    sources = [
        {"param": "participant_payee_ids", "source": _utt(10, 12)},
        {"param": "participant_payee_ids", "source": _utt(13, 15)},
        {"param": "participant_payee_ids", "source": _utt(16, 18)},
        {"param": "account_id", "source": {"kind": "read", "ref": "rd_1", "pointer": "/x"}},
    ]
    out = _merge_list_sources(_plan(sources))["steps"][0]["arg_sources"]
    split = [s for s in out if s["param"] == "participant_payee_ids"]
    assert split == [{"param": "participant_payee_ids", "source": _utt(10, 18)}]
    assert any(s["param"] == "account_id" for s in out)


def test_mixed_kinds_or_refs_are_left_for_the_contract():
    mixed = [
        {"param": "participant_payee_ids", "source": _utt(10, 12)},
        {
            "param": "participant_payee_ids",
            "source": {"kind": "read", "ref": "rd", "pointer": "/a"},
        },
    ]
    assert len(_merge_list_sources(_plan(mixed))["steps"][0]["arg_sources"]) == 2
    refs = [
        {"param": "participant_payee_ids", "source": _utt(10, 12, "utt_a")},
        {"param": "participant_payee_ids", "source": _utt(1, 3, "utt_b")},
    ]
    assert len(_merge_list_sources(_plan(refs))["steps"][0]["arg_sources"]) == 2
