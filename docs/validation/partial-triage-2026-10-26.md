# Partial-answer triage — 2026-10-26

Source: `docs/validation/live-evaluation-2026-10-19.json` (140 questions).
The run contains **81 answered, 18 partial, 41 abstained**. This triage uses the
recorded gaps, answer claims, latency and review status; it does not re-run the
model.

Some rows have more than one contributing issue. The table assigns one primary
reason so that the next changes can be measured separately.

| Primary reason | Count | IDs |
|---|---:|---|
| Calculation not completed | 3 | `semantic-causality-004`, `semantic-causality-007`, `semantic-causality-019` |
| Evidence missing from the corpus/retrieval | 5 | `semantic-causality-015`, `semantic-risk-007`, `semantic-risk-013`, `semantic-risk-015`, `semantic-risk-016` |
| Timeout / model failure | 5 | `semantic-causality-011`, `semantic-causality-020`, `semantic-risk-001`, `semantic-risk-010`, `semantic-risk-019` |
| Correct refusal / scope limitation | 4 | `semantic-negation-003`, `semantic-negation-009`, `semantic-negation-014`, `semantic-negation-017` |
| Review independence not established | 1 | `semantic-risk-012` |

## What the categories mean

- **Calculation not completed:** the answer contains verified facts but lacks a
  requested growth, margin or comparison calculation. Adding more prose would
  not fix it; the missing typed result must be produced or explicitly listed as
  a gap.
- **Evidence missing:** the answer preserves a valid limitation because the
  filing does not provide the requested quantitative attribution, customer
  concentration number or passage. These must not be converted into complete
  answers by prompts alone.
- **Timeout / model failure:** the run hit the old 420s research budget or the
  provider failed; verified numeric evidence was retained but the narrative
  review did not finish. These are the first targets for the 300s budget and
  review-reserve experiment.
- **Correct refusal / scope limitation:** the negation answers explicitly
  distinguish “not stated in the filing” from “the proposition is false.” They
  should stay partial/limited unless a broader source is added; forcing them to
  complete would increase false certainty.
- **Review independence:** the answer may be usable, but the review model could
  not be confirmed independent. This is a configuration/observability issue,
  not a retrieval failure.

## Next measurement

Do not use the overall complete rate as the first target. Use:

1. calculation-completion rate on the three calculation rows;
2. evidence-coverage rate for each requirement in the five evidence-missing rows;
3. timeout/truncation rate and stage timings on the five timeout rows;
4. correct-refusal preservation on the four negation rows;
5. review independence rate on the full 20-question stratified run.

The 18 rows should be replayed first, then a 20-question stratified API set.
