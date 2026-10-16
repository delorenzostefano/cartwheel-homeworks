# Notes on frontier.csv

## Configuration 1 (claude-opus-5-5) is not a valid measurement

Every one of its 12 test runs returned an empty reply: 43,675 input tokens, 0 output tokens, about 2 seconds each, and no recorded error. The empty reply still passed two checks:

- e-007 passed `reply_not_contains`, because an empty reply contains nothing.
- e-010 passed the `unrequested_detail` judge, which found "no assistant final reply" and nothing to fail.

Its score of 0.5 and cost of $1.46 per 100 conversations therefore measure an agent that never answered. They do not measure Opus 5.5 on the starting configuration. The `optimize.check_models` run before preparation showed the same pattern (`ok: true, passed: false`). The check only looks for agent errors, so it did not catch an empty reply.

### Diagnosis

One direct call to `claude-opus-5-5`, made after the batch with the agent's own model settings (`reasoning.effort = "high"` through LiteLLM), returned normal text: 35 input tokens, 50 output tokens. A plain call with those settings therefore works. The empty replies happen only in the full agent setting, with the system prompt and tools; about 3.6k input tokens per run is consistent with a single model request per run. The exact cause was not confirmed further.

### Decision

The test batch runs once, so configuration 1 was not rerun. Rerunning it would require editing `optimize/state/test_run.json` and changing agent settings at the frozen starting commit. The cost-and-score row for Opus 5.5 is reported as invalid.

## The comparison that is valid

Configurations 3 and 4 use the same model (`claude-haiku-5-5`), so the difference between them is the effect of the agent changes:

| Configuration | Agent | Score | write_pass_5 | Cost per 100 conversations |
| --- | --- | ---: | ---: | ---: |
| 3 | starting | 0.25 | 0.50 | $0.43 |
| 4 | final | 0.55 | 0.50 | $0.22 |

- e-007 (`policy_id_in_reply`) failed under configuration 3 and passed under configuration 4.
- e-003 (eligible refund above $100) passed 0 of 5 runs under configuration 3 and 1 of 5 under configuration 4.
- e-005 passed 5 of 5 runs under both.
- e-010 failed the judge under both.

Configuration 4 dominates configurations 2 and 3, and it is the only configuration on the measured frontier.

The final configuration scored 1.0 on the development cases and 0.55 on the test cases. The final agent configuration was not changed after the test results were read.
