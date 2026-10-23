Most RAG demos stop at a fluent answer.

In finance, the answer is the easy part. The hard part is proving where every number came from — and letting a second person re-check it after the model, the index, or the machine changes.

I built FilingCopilot around that second part:

- Every fact is tied to a filing, period, currency, unit, and line locator.
- Deterministic numeric gates and a semantic reviewer check claims before they are shown.
- Every answer exports as a frozen package with a SHA256 manifest and verifies offline, without the agent or model.
- The deterministic path answers annual numbers and trends with no LLM at all.

On the recorded evaluation: 140 bilingual questions; 81/100 research questions complete, 18 partial, 1 no answer. 116 numeric claims matched SEC CompanyFacts or an arithmetic check with 0 mismatches.

The honest limitation: the evaluation is self-reviewed, there is no independent human gold standard, retrieval Recall@5 is 0.430 after the coverage rerank (0.255 lexical baseline), and model-using questions had a P95 of 417 seconds. I publish those as weaknesses, not footnotes.

I also mapped the same evidence layer to a synthetic credit-risk corpus: policy clauses, model-validation findings, and portfolio tables. Same package, same verifier, different domain.

Code and case study: https://github.com/francisown/filingcopilot

#LLM #RAG #AIEngineering #ModelRisk
