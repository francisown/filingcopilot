# Evaluation and limitations

## What the existing numbers measure

The frozen 140-question production-budget experiment retained 40 answered,
67 partial and 33 abstained records. Three answered records missed a declared
character minimum; 37/140 (26.43%) met both workflow status and that length rule.
This is a mechanical delivery measure, not human answer completeness or truth.
Evidence-ID binding was 75.91%. A first human calibration of the 40 delivered
narrative/analysis claims recorded 95% support / 5% unclear / 0% unsupported —
one first pass by the project author as a single non-domain reviewer, with
delivered claims carrying the review gate's selection bias; an independent
domain review remains future work. The review miss-rate remains null pending
its calibration key.
P50/P95 were 135.93/240.03 seconds under the old 240/60-second budgets.

[Budget methodology and original records](user-guide.md#public-release-notes)
remain available. Do not overwrite old results or combine runs with different
source snapshots, configurations or time budgets.

## Retrieval

On the frozen 200-question editorial exact-passage set, the active focused-query
75-candidate path recorded recall@5 0.260 and MRR@5 0.150333 under the calibrated
anchor coverage weight 0.05 (earlier weight-0.01 calibration: 0.240/0.132083).
The full-query 25-candidate control was 0.155/0.084333. The doubling target was not met.
In the active-path diagnosis, 65 questions missed the target in the candidate
pool and 83 had a candidate target outside the returned top five. Fix these
separately. This calibration is not independently human-adjudicated semantic accuracy.

The cross-encoder and multilingual embedding candidates failed their joint
recall/MRR/latency gate and remain disabled. No candidate or gold change is
part of the current release. [Negative result](rerank-negative-result-2026-10-11.md).

## Targeted remediation

A retained failure asked for NVIDIA FY2022 revenue and net income, spent
178.137 seconds and abstained. The original corpus had no FY2022 facts; the
model repeated tools and inspected other years. The new typed-number path can
retrieve the requested annual end-years without vector indexing or financial
model narration. Reasons, outlook and forecasts stay on the analysis path.
Validate actual source acquisition, dates, missing metrics, cache reuse and
package replay; this single repair does not establish a new 140-question score.

## Human acceptance

The original 20/40 worksheets and 872 paired-comparison units are preserved.
Only a real person's labels and identity may be imported. Mechanical token
hints never score. Partial reviewed subsets need their selection-bias caveat;
a first single-reviewer, non-domain calibration over 40 delivered claims is
recorded (95% support / 5% unclear / 0% unsupported; one first pass by the
project author), and the review miss-rate remains null until its required
inputs exist. Original acceptance-stage checks remain pending; later stages are not unlocked by rule tests.

## Verification scope

Passing tests or offline consistency checks does not certify narrative truth,
price-provider correctness, issuer authenticity or investment performance.
Source caches, unrounded calculation inputs, source dates, currencies and share
bases remain part of evidence. Missing ADS ratio, dated FX or unrounded inputs
are never filled by guessing.
