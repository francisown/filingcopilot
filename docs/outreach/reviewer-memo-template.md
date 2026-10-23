# Independent review memo — template

**Purpose:** obtain a small, honest, independent review of the narrative claims produced by FilingCopilot. This is not a certification and not a regulatory validation. It is a check on whether the claims stay within the cited evidence.

## Reviewer

- Name:
- Role / domain experience:
- Organization (optional):
- Date:
- Conflicts of interest (financial, employment, personal):
- Did you contribute to the project? Yes / No

## Materials provided

- Package IDs or file paths:
- Question set version / hash:
- Source documents (or corpus snapshot hash):
- Number of claims sampled:
- Sampling method (random / stratified / all):
- Were model rationales removed before review? Yes / No
- Were answers blinded to the author? Yes / No

## Scope

Review **narrative and analytical claims only**. Numeric claims are checked separately by the deterministic oracle and are out of scope for this memo.

For each claim, open the cited passage and judge whether the claim is supported by that passage, not by outside knowledge. If the passage is insufficient, mark **Cannot assess** and say what would be needed.

## Rubric

| Verdict | Definition |
|---|---|
| **Supported** | The cited passage directly supports the claim, including subject, direction, period, and scope. |
| **Partially supported** | The passage supports part of the claim, but a qualifier, period, magnitude, or scope is missing or overstated. |
| **Unsupported** | The passage contradicts the claim, or the claim goes beyond what the passage says. |
| **Cannot assess** | The passage is missing, ambiguous, or insufficient for an independent judgment. |

## Claim review sheet

| # | Claim ID | Question | Claim (short) | Evidence locator | Verdict | Note |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |
| 4 | | | | | | |
| 5 | | | | | | |

Add rows as needed. Keep notes short and specific; quote the passage if the verdict depends on a phrase.

## Summary

- Claims reviewed:
- Supported:
- Partially supported:
- Unsupported:
- Cannot assess:
- Main disagreement themes (2–4 bullets):

## Attestation

I reviewed the sampled claims independently. I did not use the system's own answer or rationale as ground truth. I did not author the system. My verdicts reflect the cited evidence only.

Signature: ____________________  Date: ____________________

## Permission

- May the author publish an anonymized summary of this review? Yes / No
- May the author quote the disagreement themes without attribution? Yes / No
- Preferred attribution (optional):

## Notes for the author

1. Sample 30 claims from at least 5 packages, stratified by question type (numeric, cause, risk, negation, comparison).
2. Remove model rationales and review verdicts before sending. Provide only the claim, the question, and the cited source passage.
3. Do not select only successful claims. Include partial and abstained cases.
4. Publish the disagreement rate even if it is worse than the self-review. The point is credibility, not a better number.
