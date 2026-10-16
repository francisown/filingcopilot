# Local demonstration

This is a reproducible workflow, not a claimed three-minute recording or a
public hosted demo. It uses the local app and exports a portable research package.

1. Run `start.ps1 -Port 8159` and select NVIDIA / NVDA.
2. Ask `What were NVIDIA's FY2022 revenue and net income?` in Agent mode.
3. Inspect FY2022 source dates, currency and each fact's original filing link.
   The typed financial path does not call a model or wait for a vector index.
   Missing sources/metrics stay explicit; other years are not substituted.
4. Export the package and run `filingcopilot verify-package package.json`.
   For original-byte checks add the trusted corpus and source-cache arguments
   shown in [independent verifier instructions](independent-verifier.md).
5. Compare a causal question such as `Why did revenue change?`: this needs
   original passages and model review, and retains an independent status.

The first SEC download needs a real contact configured in `.env` and available
network access. An unregistered Chinese company name may need one model call
for name conversion; it is accounted separately from financial computation.

[Current acceptance index](../artifacts/README.md) lists retained evidence and
limits. A 140-question production-budget re-evaluation is not implied by one
working example or by deterministic fixture tests.
