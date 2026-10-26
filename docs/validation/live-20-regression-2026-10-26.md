# 20-question live regression triage — 2026-10-26

Source: frozen 20-question run in `docs/validation/live-20-2026-10-26/`,
compared with the same 20 IDs in `live-evaluation-2026-10-19.json`.

## Headline

- Current: **11 answered / 9 partial / 0 abstained**
- Historical same IDs: **17 answered / 3 partial / 0 abstained**
- Net change: **7 answered→partial, 1 partial→answered, net -6**
- P50/P95: **47.8s / 152.1s**
- Numeric mismatch: **0**
- Citation binding: **1.0**
- Timeout / runner errors: **0 / 0**

No lost-answer row had `budget_timeout`, a gateway error, or a runner error.
The regression is therefore **not caused by the 300s budget** and is not fixed
by simply increasing the model budget.

## The 7 answered→partial rows

| ID | Primary reason | Secondary reason | Evidence |
|---|---|---|---|
| `semantic-causality-002` | New strict verification downgrade | Retrieved MD&A lacks consolidated total-net-sales causal sentence and full-year iPhone/Mac driver excerpts | 2 verification findings; 6 answer claims; 136.3s |
| `semantic-negation-001` | Correct refusal/scope limitation | Cannot prove document-wide absence from focused excerpts | 2 verification findings; 4 claims; 53.8s |
| `semantic-negation-002` | New strict verification downgrade | Document-wide absence not established | 6 verification findings; 4 claims; 82.1s |
| `semantic-negation-004` | Correct refusal/scope limitation | No explicit denial in reviewed excerpts; document-wide absence not established | 2 verification findings; 4 claims; 69.6s |
| `semantic-risk-002` | Evidence insufficient | No quantified single-customer share in retrieved excerpts | 3 verification findings; 10 claims; 100.6s |
| `semantic-risk-003` | Evidence/coverage limitation | Not every risk-factor heading or quantitative exposure was retrieved | 0 verification findings; 8 claims; 152.1s |
| `semantic-risk-004` | Evidence insufficient | No specific customer-concentration risk found; credit risk substituted | 2 verification findings; 8 claims; 149.0s |

One reverse move:

| ID | Old | Current | Notes |
|---|---|---|---|
| `semantic-risk-001` | partial | **answered** | 6 claims; 100.3s; improvement |

## What this means

- **Deadline/model/gateway:** 0 rows. The 300s budget is not the cause of the
  complete-rate loss; P95 also passed at 152s.
- **Strict verification:** at least 4 rows are downgraded by claim verification
  or explicit absence limitations (`causality-002`, `negation-002`, `risk-002`,
  `risk-004`).
- **Correct scope limitation:** `negation-001` and `negation-004` should remain
  partial unless a broader source is added; converting them to answered would
  be an overclaim.
- **Evidence coverage:** `risk-003` needs a fuller risk-heading retrieval pass
  before it can be answered; it is not a strict-review failure.
- **Review quality:** the partial rows contain review/verification findings;
  there is no evidence that review was skipped or weakened to gain speed.
- **Answered target 16/20:** cannot be reached by relaxing verification. It
  requires better multi-evidence retrieval/proof for the causality/risk rows,
  while leaving the two negation scope limitations partial or explicitly
  refused.

## Next action

Implement an evidence-graph/support report with three layers:

1. evidence retrieval;
2. input coverage (numbers, units, periods, calculation inputs);
3. answer proof (including formula verification).

Then replay the 20 rows. Do not run the 140-question set first.
