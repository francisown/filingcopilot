# X / Twitter thread

**1/8**
Most RAG demos stop at a fluent answer.
In finance, the answer is the easy part. The hard part is proving where every number came from — and letting someone else re-check it later.
I built FilingCopilot around that second part. 🧵

**2/8**
The flow is deliberately boring:
question → research service → LangGraph agent → hybrid retrieval → evidence ledger → deterministic gates → answer + gaps → frozen package → offline verifier.
No magic. The evidence is the product.

**3/8**
Every fact carries company, filing, public date, period, currency, units, source URL, and locator.
Every calculation carries its inputs and formula.
A citation is a pointer to a stored object, not a string the model produced.

**4/8**
Verification is layered:
- numeric checks vs SEC facts / arithmetic
- currency-direction checks
- document-scope checks (no “filing doesn’t mention it” from excerpts)
- semantic review: subject, period, materiality, scope
Passing review ≠ correct. The package shows gaps.

**5/8**
The package is frozen with a SHA256 manifest.
The standalone verifier re-checks package hash, document hash, and quote span — no agent, no model, no network.
If a source changes, the hash fails. If a quote is edited, the span fails.

**6/8**
Recorded evaluation:
- 140 bilingual questions
- 81/100 research questions complete, 18 partial, 1 no answer
- 116 exact SEC/arithmetic matches, 0 mismatches
- 1,656 tests at the recorded source (1,715 now)
- 30 MCP tools

**7/8**
The honest weaknesses:
- self-reviewed; no independent human gold standard
- retrieval Recall@5 = 0.26 (target not met)
- model-using questions: median 92.8s, P95 417s
- 4.3M tokens in the recorded run
I publish these because a demo without limitations is marketing.

**8/8**
I also mapped the same evidence layer to a synthetic credit-risk corpus: policy clauses, SR 11-7-style validation findings, portfolio tables.
Same package, same verifier, different domain.
Code + case study: https://github.com/francisown/filingcopilot
