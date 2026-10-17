# Evaluating and improving an LLM customer-support agent

End-to-end evaluation work on a tool-using support agent: error analysis, an LLM judge validated against human labels, CI regression gates, production monitoring, and accuracy and cost optimization measured on a held-out test set.

> **Context.** This repository is my fork of the starter code for the course *Evaluating and Improving AI Agents*. The course provided the agent scaffolding, the synthetic commerce world, and the evaluation harness. The tools, analyses, judges, CI, monitoring, experiments, and results described below are my work.

## The system

**Cartwheel** is a fictional multi-store commerce platform (20 stores, 10,000 orders, 18 policy documents). Its support agent, built on the OpenAI Agents SDK and run on Claude Haiku 5.5, answers shoppers, merchants, and support staff using 14 tools, such as order lookup, refunds, and policy search. Refunds above $100 are queued for human approval, and every tool enforces role-based authorization.

## Results at a glance

| Stage | Result |
| --- | --- |
| Error analysis | 125 conversations reviewed by hand; 6 failure modes defined, each with a requirement and an evaluator |
| LLM judge | Validated on 79 held-out labels: it catches 79% of real failures but passes only 48% of good replies, so I report it as not ready for automated decisions |
| CI | 13 cases run with 5 trials each on every pull request; an intentional regression was blocked (0 of 5 runs passed) and passed again after the revert |
| Accuracy | Development score **0.29 → 1.00**; refunds passing all 5 runs **25% → 100%** |
| Held-out test | **0.25 → 0.55** on the same model, at **half the cost** ($0.43 → $0.22 per 100 conversations) |
| Cost | Cost profiled per call site; prompt caching and a model cascade measured, and the cascade rejected against a requirement set in advance |

## What I built

| Area | What I did | Where |
| --- | --- | --- |
| Agent tools | Implemented the support tools with role-scoped data access and structured results | [`agent/tools.py`](agent/tools.py) |
| Tracing | Authenticated sessions and per-tool span attributes, exported to Langfuse | [`server/`](server/), [`observability/`](observability/) |
| Test data | 250 synthetic support scenarios (shoppers, merchants, support staff, damaged records) | [`scenarios/`](scenarios/) |
| Error analysis | Open coding, a failure taxonomy, and spec revisions grounded in annotated traces | [`analysis/report/review_summary.md`](analysis/report/review_summary.md) |
| LLM judge | A judge for "unrequested detail", with TPR/TNR on development and test splits and reviewed disagreements | [`analysis/state/judges/`](analysis/state/judges/), [`analysis/report/`](analysis/report/) |
| CI | Harbor tasks plus a GitHub Actions workflow that separates regression gates from capability tracking | [`eval_cases/`](eval_cases/), [`.github/workflows/evals.yml`](.github/workflows/evals.yml) |
| Monitoring | A daily monitor on a self-hosted runner, with judge-error-corrected prevalence and confidence intervals | [`monitoring/`](monitoring/README.md) |
| Accuracy optimization | A locked dev/test split, manual changes at the prompt, tool, and harness layers, an automated improve loop, and a Pareto frontier on held-out data | [`optimize/results/`](optimize/results/) |
| Cost optimization | Cost per call site, a token-reduction experiment, prompt-cache reordering, and a confidence-threshold model cascade | [`profile/`](profile/), [`cascade/`](cascade/) |

## Findings worth highlighting

- **The biggest accuracy gain came from a prompt bug, not the model.** Refunds above $100 failed because one prompt line told the agent to escalate them, contradicting the spec. Fixing that line took the refund cases from 4 of 15 passing runs to 15 of 15, and lowered cost.
- **I measured prevalence before fixing.** A reviewed sample suggested 61% of replies leaked internal policy IDs. A code check, validated against 128 human labels with 100% agreement and run over all 251 conversations, gave the real figure: 45%.
- **Noise was as large as the effects.** The same unchanged agent scored 0.82 and 0.64 on two runs, so I kept only changes whose effect was far larger than that spread, and treated small differences as ties.
- **I caught an invalid measurement.** One frontier configuration (Claude Opus 5.5) returned empty replies on every run, but still "passed" two checks. I documented it as invalid instead of reporting its score ([`optimize/results/frontier-notes.md`](optimize/results/frontier-notes.md)).
- **I rejected a cascade on evidence.** A local 8B proxy judge reported 95–100% confidence on almost every verdict, yet agreed with the reference judge only 68–71% of the time. Against a 95% requirement set before the run, I rejected it.
- **A metric is only as good as its reference.** The reference judge itself agreed with human labels 87% on development but 60% on test, which is the next thing to fix.

## Tech stack

Python 3.12 · OpenAI Agents SDK · LiteLLM · Langfuse · DocETL · Harbor · GitHub Actions · SQLite · Claude (Haiku, Sonnet, Opus 5.5) · DeepSeek · Groq · Ollama

## Run it

```bash
uv sync
uv run python -m seed.generate
uv run pytest
uv run python -m agent.cli --role shopper --user 1 --debug
```

Copy `.env.example` to `.env` and add a key for one model provider. The full course setup guide is in [`docs/COURSE_README.md`](docs/COURSE_README.md).

## Credits

Starter code, synthetic world, and assignments: [ai-evals-course/cartwheel-homeworks](https://github.com/ai-evals-course/cartwheel-homeworks).
