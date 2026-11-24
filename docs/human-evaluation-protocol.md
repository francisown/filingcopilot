# Independent review and pilot protocol

This project has engineering measurements and a model-assisted review pass, but
it does not yet have an independent human accuracy study. This page defines the
smallest useful study and separates it from the automated checks.

## What is being measured

The primary question is not "does the prose look fluent?" It is "can an
independent analyst reproduce the support for each delivered claim?"

Report these denominators separately:

| Measure | Definition |
|---|---|
| Claim support rate | Supported claims / claims assessed by a human |
| Misleading-answer rate | Answers with at least one contradicted or materially incomplete claim / answers assessed |
| Dispute rate | Claims where two reviewers disagree before adjudication / claims with two reviews |
| Appropriate-refusal rate | Designed insufficiency questions that receive a specific limitation rather than an answer |

Numeric claims can be checked against the deterministic oracle first. The human
sample should still include narrative, causal, negation and risk claims, because
those are the rows that model review cannot certify by itself.

## Sample

Use a frozen run that has a recorded report and results file. A practical first
study is 60 delivered answer rows:

- 20 causal or explanatory answers;
- 20 negation or exact-wording answers;
- 10 risk answers;
- 10 numeric or cross-period answers, used as a control.

Stratify by ticker and category; do not select only rows that passed review.
Keep rejected drafts and partial answers in the sampling frame where the run
retained them, and report them separately rather than dropping them. If the
frozen corpus is available, export 60 narrative or analysis claims instead and
report the claim denominator; do not mix the two denominators in one rate.

## Reviewers

Recruit two or three people with financial-analysis, credit-risk, audit or
accounting experience. Reviewers must not be the project author. Give each
reviewer the same blind worksheet, the cited evidence, and the filing links.
Do not show the model identity, verification status, expected behavior, or the
other reviewer's labels.

Each reviewer marks every claim:

- **Supported** — the cited evidence entails the claim as written.
- **Contradicted** — the cited evidence contradicts the claim, or the claim
  reverses a direction, period, unit or negation.
- **Insufficient** — the evidence is relevant but does not establish the claim.
- **Not assessable** — the supplied evidence is missing or unreadable.

For an answer-level judgment, use **complete**, **partial with explicit gap**,
**misleading**, or **appropriate refusal**. A fluent answer with an unsupported
claim is misleading, not partial.

## Workflow

1. Export a blind worksheet from the frozen results. This command samples
   delivered answer rows and does not create a decision key. If you need
   claim-level review and have the frozen run corpus, add
   `--home <frozen-run-home> --review-key artifacts\human\blind-key.json`; keep that key separate and
   do not send it to reviewers. Do not pass `--reviewed-only` for the
   answer-level study.

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\export_semantic_blind_sample.py `
     artifacts\live-run\results.json `
     --size 60 `
     --output artifacts\human\blind-sample.json
   ```

   The first argument is the `results.json` from one of your own local runs;
   raw run records are not committed.

2. Build the offline review page. It has no network calls and does not contain
   the private decision key.

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\build_blind_review_desk.py `
     artifacts\human\blind-sample.json `
     --output artifacts\human\review.html
   ```

3. Send `review.html` and the filing links to each reviewer. Ask them to return
   the exported labels with their name or pseudonym.

4. Score each reviewer separately, then adjudicate disagreements. Never replace
   a missing label with a model judgment.

   ```powershell
   .\.venv\Scripts\python.exe .\scripts\score_semantic_blind_labels.py `
     artifacts\human\blind-sample.json `
     --reviewer R1 `
     --output artifacts\human\r1-score.json
   ```

   Add `--review-key artifacts\human\blind-key.json` only for a claim-level
   sample that was exported with a separate key.

5. Publish the protocol, the sample hash, the reviewer count, the label
   distribution, the disagreement rate and the adjudicated result. Do not
   publish reviewer identities without consent.

## Minimum independent-review protocol

The automated review pass is self-review and does not satisfy this protocol.
Before any claim of human relevance or financial correctness:

- Draw from delivered answers, rejected drafts, refusals and numeric-only answers,
  not only successful claims. A minimum practical target is 60 questions with at
  least 10 from each stratum (metrics-generated, annual comparison,
  risk/qualitative, negation, and refusal).
- Reviewers see the question, answer, cited evidence and package; they do not see
  the model name or the author's intended verdict.
- For each answer record separately: claim support, period/unit/currency/subject
  correctness, arithmetic from cited inputs, disclosed assumptions and missing
  evidence, overconfidence or investment-advice wording, and whether the reviewer
  would trust it as a research draft.
- Resolve disagreement with a third reviewer or retain it as a reported
  disagreement; do not average it away.
- Report raw counts and 95% intervals, the reviewer identity class, and
  false-support examples, not only aggregate percentages. Keep self-review and
  independent review in separate columns.

**Status: pending.** No independent domain reviewer has completed this protocol.
The self-review and retrieval measurements are not a substitute for it.

## User-value pilot

A second, smaller study measures whether the workflow saves work. Pick one real
task per participant, such as "summarize the last two years of margin drivers
for one issuer" or "find and verify every disclosed customer-concentration
risk". Record:

- baseline time and steps without the tool;
- time to first useful evidence;
- total time to a reviewed answer;
- number of corrections the analyst made;
- whether the analyst would use it again for the same task.

A time saving is only meaningful if the reviewed answer meets the same standard
as the baseline. Report abandoned tasks and corrections, not only successes.
Until these studies are complete, README and evaluation copy must say
"engineering measurement, not independent human accuracy."
