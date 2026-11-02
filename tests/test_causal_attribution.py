"""Observational disclosures cannot become attributed causal explanations."""
import pytest

from filingcopilot.models import Claim, Submission
from filingcopilot.verification import verify_submission


@pytest.mark.parametrize("statement", [
    "Orders increased, therefore the delivery pattern changed.",
    "Orders increased; as a result, the delivery pattern changed.",
    "订单增加，因此交付模式发生了变化。",
    "订单增加，交付模式因而改变。",
])
def test_observational_source_does_not_support_added_causal_link(tools, statement):
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of),
                          "text": "Orders increased and the delivery pattern changed. These trends may not continue."}
    called = []
    def approving_reviewer(question, claims):
        called.extend(claims)
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "An overpermissive review"}
                             for c in claims]}
    accepted, errors = verify_submission(Submission(claims=[Claim(kind="narrative", statement=statement,
                                                                evidence_ids=["p1"])]), tools,
                                        "Explain the operating change.", approving_reviewer)
    assert not accepted and any("causal" in error.casefold() for error in errors)
    assert not called, "A review approval cannot manufacture source attribution"


@pytest.mark.parametrize("statement,source", [
    ("Orders increased and delivery patterns changed.", "Orders increased and delivery patterns changed."),
    ("Orders increased, therefore delivery patterns changed.", "Delivery patterns changed because orders increased."),
    ("订单增加，因此交付模式改变。", "Delivery changes were driven by higher orders."),
    ("Costs fell due to efficiency gains.", "Lower costs reflect improved operating efficiency."),
    ("较长交期可能导致供需错配。", "Long lead times could lead to a mismatch between supply and demand."),
    ("设计变更可能导致库存过剩。", "Changes to the product design could trigger excess inventory."),
    ("交期缩短因而要求减少在途存货。", "Shorter lead times require less inventory in transit."),
    ("供应错配可能导致库存过剩。", "Mismatches may give rise to excess inventory."),
    ("The source therefore does not establish a causal link.", "Orders increased and delivery patterns changed."),
])
def test_supported_attribution_or_scoped_limitation_still_reaches_review(tools, statement, source):
    tools.ledger["p1"] = {**tools.corpus.evidence("p1", tools.as_of), "text": source}
    called = []
    def approving_reviewer(question, claims):
        called.extend(claims)
        return {"verdicts": [{"index": c["index"], "supported": True, "reason": "Source relation checked"}
                             for c in claims]}
    accepted, errors = verify_submission(Submission(claims=[Claim(kind="narrative", statement=statement,
                                                                evidence_ids=["p1"])]), tools,
                                        "Explain the operating change.", approving_reviewer)
    assert accepted and called, errors
