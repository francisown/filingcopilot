import json
from pathlib import Path


def test_retrieval_gold_set_is_versioned_and_has_frozen_targets():
    path = Path(__file__).parents[1] / "evaluation" / "retrieval_gold_v2.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == "filingcopilot.retrieval-gold/v2"
    assert payload["count"] == len(payload["items"]) >= 200
    assert len({item["ticker"] for item in payload["items"]}) >= 5
    assert {item["language"] for item in payload["items"]} == {"en", "zh"}
    assert all(item["expected_passage_ids"] for item in payload["items"])
    assert all(item["gold_context"]["section"] for item in payload["items"])
    assert all(item["expected_passage_ids"][0].startswith("passage-") for item in payload["items"])
