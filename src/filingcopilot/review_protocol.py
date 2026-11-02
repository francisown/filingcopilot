"""Scalar review transport; evidence and inference are separate decisions."""


def review_tool(indexes: list[int], gap_indexes: list[int], *, resolution: bool = False,
                coverage_index: int | None = None) -> dict:
    properties = {}
    for index in indexes:
        properties[f"claim_{index}_scope_check"] = {
            "type": "string",
            "maxLength": 800,
            "description": "Check EACH assertion's actor, geography, segment, accounting line, period and qualifiers. Bind product/segment directions to their own source rows, never to a consolidated growth calculation. For listed charges, map each to its stated accounting line; existence of all charges does not prove their allocation. Operating-income impact does not imply Other operating expense. An effect alone does not prove materiality; no material impact does not mean no harm or no incident. Report conflicting/unproved bindings first; omit irrelevant matching background. Use at most 600 characters.",
        }
        properties[f"claim_{index}_scope_supported"] = {
            "type": "boolean", "description": "Every asserted scope and accounting classification matches its source. If any listed charge is assigned to an unproved or wrong expense line, return false for the entire claim. A regional/segment fact does not establish a company-wide fact.",
        }
        properties[f"claim_{index}_source_supported"] = {
            "type": "boolean", "description": "Every factual premise matches its cited source, period and scope.",
        }
        properties[f"claim_{index}_inference_supported"] = {
            "type": "boolean", "description": "Every conclusion follows without a scope, polarity, certainty or materiality jump. Possible harm does not refute absence of realized harm. A nonzero effect does not refute immateriality; favorable/adverse direction or a percentage alone is not a disclosed materiality assessment.",
        }
        properties[f"claim_{index}_document_absence"] = {
            "type": "boolean",
            "description": "True if ANY sentence asserts that a statement is absent across an entire filing based only on selected passages, even if another sentence limits scope. False for a directly reported negative financial fact, an observation limited to the cited passages, or a bounded negative_evidence full-filing keyword-scan result stating that no indexed passage matched the explicit pattern without generalizing beyond it.",
        }
        properties[f"claim_{index}_reason"] = {"type": "string", "maxLength": 800}
        if index == coverage_index:
            properties[f"claim_{index}_scope_check"]["description"] = (
                "This is an INTERNAL whole-answer completeness check, not a financial assertion. "
                "Using only supported claims in complete_answer_context, identify any unanswered "
                "requested point. A change explanation must explain the net direction of the "
                "requested metric, not merely list figures, opposing forces or say an explanation "
                "is needed. Named risk categories must be covered. Do not demand optional details, "
                "an exhaustive risk catalogue, a full dollar bridge or a single management quote "
                "when supported component synthesis answers the question. Name the missing point "
                "and a useful source-language retrieval phrase if incomplete; otherwise explain "
                "briefly how the supported answer covers the request. At most 600 characters."
            )
            for suffix in ("source_supported", "inference_supported", "scope_supported"):
                properties[f"claim_{index}_{suffix}"]["description"] = (
                    "True only if the SUPPORTED answer covers the requested explanation/categories. "
                    "Exclude claims rejected in this review or prior_verdicts. Correct individual "
                    "sentences alone do not establish whole-answer completeness."
                )
            properties[f"claim_{index}_document_absence"]["description"] = (
                "Return false for this internal completeness check; assess document-absence "
                "claims separately at their actual claim indexes."
            )
        if resolution:
            properties[f"claim_{index}_proposition_relation"] = {
                "type": "string", "enum": ["supports", "refutes", "observation", "unresolved"],
                "description": "Classify what the claim asserts about the QUESTION's proposition, before checking whether that assertion is justified. A claimed contradiction/refutation is refutes even when it is unsupported. Distinguish evidence sufficiency from factual polarity: 'these excerpts cannot establish P' is unresolved, not refutes. 'These excerpts do not contain an explicit statement of P' is an observation, not refutes. Only a claim that P itself is false or contradicted is refutes. Apply the same distinction to supports. An accurately scoped uncertainty statement must not be rejected just because the overall proposition is unresolved.",
            }
    for index in gap_indexes:
        properties[f"gap_{index}_required_missing"] = {"type": "boolean"}
        properties[f"gap_{index}_description_supported"] = {
            "type": "boolean",
            "description": "Every factual assertion in this limitation is supported by the evidence or research_operations. An unresolved question does not license an invented explanation about missing tools, files, failed searches or review. Judge truth separately from whether evidence is still missing.",
        }
        properties[f"gap_{index}_search_query"] = {
            "type": "string", "maxLength": 180,
            "description": "If a required point is missing, give a short focused retrieval query in the language of the SOURCE passages (usually English), even when the question is Chinese. Name the missing subject and metric, not the whole question or a guessed answer. Company/filing/year filters are enforced separately. Empty when no follow-up evidence is needed.",
        }
        properties[f"gap_{index}_reason"] = {"type": "string"}
    if resolution:
        properties.update({
            "answer_resolution_evidence_id": {"type": "string"},
            "answer_resolution_quote": {
                "type": "string",
                "description": "One or more complete verbatim source sentences, including conditions. No clipped clauses or ellipses. Empty if unresolved.",
            },
            "answer_resolution_period_quote": {
                "type": "string",
                "description": "Complete verbatim sentence(s) from the SAME cited passage that explicitly date the decisive event to the requested fiscal year. Do not use filing metadata or an unrelated event's date. May equal answer_resolution_quote. Empty if the period is unstated or the outcome is unresolved.",
            },
            "answer_resolution_scope_check": {
                "type": "string",
                "description": "Before deciding, compare event period (not filing date), actor, business scope and materiality with the exact question. Explain mismatches or why they match. Unspecified past events do not establish requested-year events.",
            },
            "answer_resolution_scope_supported": {
                "type": "boolean",
                "description": "The decisive evidence explicitly settles the requested proposition with matching event period, business scope and materiality. Conditional risks and evidence restricted to another scope cannot settle it.",
            },
            "answer_resolution": {"type": "string", "enum": ["supported", "refuted", "unresolved"]},
            "answer_resolution_reason": {"type": "string"},
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
        scope = payload.get(f"claim_{index}_scope_supported")
        scope_check = payload.get(f"claim_{index}_scope_check")
        if not isinstance(scope_check, str) or not scope_check.strip():
            raise ValueError("Review must compare each claim's scope with its cited evidence")
        if any(type(value) is not bool for value in (source, inference, document_absence, scope)):
            raise ValueError("Source, inference and scope verdicts must be JSON booleans")
        reason = payload.get(f"claim_{index}_reason")
        if document_absence:
            reason = "Selected passages cannot establish document-wide absence. Limit this observation to the cited evidence."
        elif not scope:
            reason = scope_check
        verdicts.append({"index": index, "supported": source and inference and scope and not document_absence,
                         "reason": reason})
    result = {"verdicts": verdicts}
    if gap_indexes:
        result["gap_verdicts"] = [
            {"index": index, "required_missing": payload.get(f"gap_{index}_required_missing"),
             "description_supported": payload.get(f"gap_{index}_description_supported"),
             "search_query": payload.get(f"gap_{index}_search_query"),
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
