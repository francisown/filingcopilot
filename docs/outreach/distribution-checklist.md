# Distribution checklist — 2 weeks

The goal is not virality. The goal is **three qualified conversations** and **two independent reviewer responses**. A repo with 20 stars and one bank risk reviewer is worth more than 500 stars and no interviews.

## Week 1 — make it legible

### Day 1 — repository surface

- [ ] Merge the portfolio branch into `main`.
- [ ] Pin the repository on the GitHub profile.
- [ ] Confirm the README first screen renders: headline, screenshot, metrics, architecture, fast path.
- [ ] Add a social preview image (repository Settings → Social preview).
- [ ] Check every new link: case study, credit-risk README, demo script, architecture SVG.
- [ ] Add a short “What this is / what this is not” note near the top if it is missing.
- [ ] Confirm the CI badge is green on the pushed commit.

### Day 2 — professional network

- [ ] Publish the LinkedIn post (`linkedin-post.md`).
- [ ] Publish the X thread (`x-thread.md`).
- [ ] Send the reviewer memo template to 3 credit-risk colleagues. Ask for 30 claims, not a general opinion.
- [ ] Send the repo to one AI engineer for a code-level critique, not a star.

### Day 3 — technical communities

- [ ] Show HN (`show-hn.md`), Tuesday–Thursday, 08:00–10:00 ET. Stay available for 3 hours.
- [ ] r/LocalLLaMA — focus on local-first, no API key, offline verifier.
- [ ] r/MachineLearning — check the self-promotion rules first; use the weekly thread if required.
- [ ] Do not cross-post the same text everywhere in the same hour. Adapt the opening line to each community.

### Day 4 — Chinese communities

- [ ] V2EX post (`zh-posts.md`, long version). Reply to technical questions; ignore star jokes.
- [ ] 即刻 post (short version).
- [ ] 小红书 post (short version; add one screenshot).
- [ ] Share in one relevant WeChat group only if the group allows project sharing.

### Day 5 — feedback loop

- [ ] Collect every substantive question. Add the top 5 to an FAQ in the README.
- [ ] Fix any broken link or confusing first-screen sentence immediately.
- [ ] Publish the case study link as a follow-up post: “What 140 questions taught me about financial RAG.”
- [ ] Record reviewer responses in a simple tracker (name, date, claims reviewed, verdict counts).

## Week 2 — convert attention into conversations

- [ ] Apply to 2–3 roles from `application-mapping.md`, not all 7. Tailor the resume bullets.
- [ ] For each application, attach the case study PDF or link, not the whole repo.
- [ ] Follow up with reviewers after 5 business days.
- [ ] Publish one technical write-up: “Why a citation is not evidence: building a verifier for a financial agent.”
- [ ] Publish one domain write-up: “From SEC filings to credit policy: the same evidence layer, a different corpus.”
- [ ] Ask one interviewer or senior engineer for a 20-minute critique. Thank them with a specific change you made.

## Metrics that matter

| Metric | Why |
|---|---|
| Qualified conversations | The only leading indicator of interviews |
| Independent reviewer responses | Converts a self-review into external evidence |
| Referrals / intros | Higher signal than raw applications |
| GitHub clones and returning visitors | Shows whether the README earns a second look |
| Issues with reproducible bugs | Shows the project is usable, not just readable |
| Stars | Vanity; track but do not optimize |

## Response templates

**“Isn’t this just ChatGPT with a prompt?”**
> The model is one component. The project’s contribution is the evidence layer: the fact ledger, deterministic numeric gates, claim-level review, frozen package, and offline verifier. The deterministic path answers annual numbers without any LLM.

**“How do you handle hallucinations?”**
> Not by claiming they are solved. I measure them: deterministic numeric checks, currency-direction and document-scope gates, semantic review, explicit gaps, and a verifier that re-checks the evidence. The evaluation is self-reviewed, and I publish the failure taxonomy.

**“Why not just use LangChain/LlamaIndex?”**
> I use LangGraph for the agent loop. The evidence package, the deterministic gates, and the standalone verifier are the parts the framework does not give you, so they are custom.

**“0 stars — why should I care?”**
> It is a new project and I am looking for technical criticism, not validation. The case study documents the evaluation, the failures, and the limitations. If the evidence model is wrong, I would like to know where.

**“Is this production-ready?”**
> No. It is a local-first engineering artifact. Latency, cost, issuer coverage, access control, and independent human review are explicit gaps.

**“Why finance / credit risk?”**
> I come from credit risk. The domain gives me a concrete standard for what “evidence” means: a policy clause, a validation finding, a period, a currency, a reproducible workpaper. That standard is the project.

## Do not

- Do not buy stars or ask for star swaps.
- Do not claim “production-ready”, “state-of-the-art”, “beats GPT”, or “regulatory compliant”.
- Do not hide the self-review, the retrieval development-set numbers (0.430 coverage, 0.255 lexical baseline), or P95 417s. State them before someone finds them.
- Do not argue with criticism in public. Ask one clarifying question, fix what is real, and move on.
- Do not post the same link in five communities at once. Space it out and adapt the framing.
- Do not apply to all seven roles with the same resume. Pick the two where the domain mapping is strongest.
