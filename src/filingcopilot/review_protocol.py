"""Scalar review transport; evidence and inference are separate decisions."""


def review_tool(indexes: list[int], gap_indexes: list[int], *, resolution: bool = False) -> dict:
    properties = {}
    for index in indexes:
        properties[f"claim_{index}_source_supported"] = {
            "type": "boolean", "description": "Every factual premise matches its cited source, period and scope.",
        }
        properties[f"claim_{index}_inference_supported"] = {
            "type": "boolean", "description": "Every conclusion follows without a scope, polarity or certainty jump. Possible harm does not refute absence of realized harm.",
        }
        properties[f"claim_{index}_document_absence"] = {
            "type": "boolean",
            "description": "True if ANY sentence asserts that a statement is absent across an entire filing, even if another sentence limits scope. False for a directly reported negative financial fact or an observation limited to the cited passages. Selected passages cannot establish document-wide absence.",
        }
        properties[f"claim_{index}_reason"] = {"type": "string"}
        if resolution:
            properties[f"claim_{index}_proposition_relation"] = {
                "type": "string", "enum": ["supports", "refutes", "observation", "unresolved"],
                "description": "Classify what the claim asserts about the QUESTION's proposition, before checking whether that assertion is justified. A claimed contradiction/refutation is refutes even when it is unsupported.",
            }
    for index in gap_indexes:
        properties[f"gap_{index}_required_missing"] = {"type": "boolean"}
        properties[f"gap_{index}_reason"] = {"type": "string"}
    if resolution:
        properties.update({
            "answer_resolution": {"type": "string", "enum": ["supported", "refuted", "unresolved"]},
            "answer_resolution_reason": {"type": "string"},
            "answer_resolution_evidence_id": {"type": "string"},
            "answer_resolution_quote": {"type": "string"},
        })
    return {"type": "function", "function": {
        "name": "submit_review",
        "description": "Review the supplied claims. Use scalar fields directly, never encoded arrays or objects.",
        "parameters": {"type": "object", "properties": properties,
                       "required": list(properties), "additionalProperties": False},
    }}


def decode_review(payload: dict, indexes: list[int], gap_indexes: list[int], *, resolution: bool = False):
    """Normalize transport only; do not coerce or repair an unsupported verdict.

    Existing integrations may still return the old array schema. Its verdicts
    remain subject to the caller's strict validation.
    """
    if "verdicts" in payload and not any(key.startswith("claim_") for key in payload):
        return payload, None
    allowed = review_tool(indexes, gap_indexes, resolution=resolution)["function"]["parameters"]["properties"]
    if set(payload) - set(allowed):
        unknown = sorted(set(payload) - set(allowed))
        raise ValueError("Unexpected review fields: " + repr(unknown[:5])[:160])
    verdicts = []
    for index in indexes:
        source = payload.get(f"claim_{index}_source_supported")
        inference = payload.get(f"claim_{index}_inference_supported")
        document_absence = payload.get(f"claim_{index}_document_absence")
        if any(type(value) is not bool for value in (source, inference, document_absence)):
            raise ValueError("Source, inference and scope verdicts must be JSON booleans")
        reason = payload.get(f"claim_{index}_reason")
        if document_absence:
            reason = "Selected passages cannot establish document-wide absence. Limit this observation to the cited evidence."
        verdicts.append({"index": index, "supported": source and inference and not document_absence,
                         "reason": reason})
    result = {"verdicts": verdicts}
    if gap_indexes:
        result["gap_verdicts"] = [
            {"index": index, "required_missing": payload.get(f"gap_{index}_required_missing"),
             "reason": payload.get(f"gap_{index}_reason")}
            for index in gap_indexes
        ]
    assessment = None
    if resolution:
        assessment = {key.removeprefix("answer_resolution_"): payload.get(key)
                      for key in allowed if key.startswith("answer_resolution_")}
        assessment["outcome"] = payload.get("answer_resolution", "unresolved")
        if assessment["outcome"] not in {"supported", "refuted", "unresolved"}:
            assessment["outcome"] = "unresolved"
        relations = {}
        for index in indexes:
            relation = payload.get(f"claim_{index}_proposition_relation")
            if relation not in {"supports", "refutes", "observation", "unresolved"}:
                raise ValueError("Review must classify each claim's relation to the proposed statement")
            relations[index] = relation
        assessment["claim_relations"] = relations
    return result, assessment
