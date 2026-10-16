# Homework 7 monitor: `unrequested_detail`

The monitor runs the frozen Homework 5 judge `unrequested_detail-v0` (`deepseek/deepseek-chat`) over Cartwheel conversations on `claude-haiku-5-5`. Two periods use the same 50 scenarios from `scenarios/monitoring_scenarios.jsonl`: **before** is the Homework 3 run (2026-10-12, 18:36 to 19:15 UTC) and **after** is one new run (2026-10-16, 12:08 to 12:16 UTC). Each period samples 10 conversations at random (20%) to estimate the rate, and judges every conversation with a policy lookup (`policy_lookup`) for inspection. The threshold, chosen before either period was judged, is a corrected failure rate of 0.30.

| Period | Random | `policy_lookup` | Raw | Corrected | 95% interval | Risk flag rate |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| before | 10 | 25 | 0.30 | 0.00 | 0.00 to 0.41 | 0.68 |
| after | 10 | 27 | 0.50 | 0.00 | 0.00 to 1.00 | 0.52 |

The correction uses the judge's Homework 5 test results: failure sensitivity 0.793, pass specificity 0.48. The chart is `prevalence.svg`; one line per period is in `history.jsonl`.

## 1. Did the corrected failure estimate move between the two periods?

No. It was 0.00 in both periods. The raw flag rate rose from 0.30 to 0.50, but that is not evidence that the agent changed. The judge flags about half of the conversations that are actually fine (pass specificity 0.48), so a raw rate between 0.30 and 0.50 is roughly what false alarms alone would produce, and the correction cannot separate either period from "no failures". The raw difference is 3 conversations out of 10, which is within sampling noise.

## 2. Do the intervals support a conclusion, or is the result uncertain?

The result is uncertain. The intervals (0.00 to 0.41 before, 0.00 to 1.00 after) overlap almost completely and both contain the 0.30 threshold. Two things make them wide: only 10 random conversations per period, and a weak judge whose TPR + TNR - 1 is 0.27, a small denominator that amplifies noise in the correction. This monitor cannot currently detect a change of the size that matters; it needs a better judge, more sampled conversations, or both.

## 3. What did the risk groups reveal that the random estimate did not?

Where the judge's flags concentrate. Conversations with a policy lookup were flagged at 0.68 before and 0.52 after, more often than the random sample. That matches the Homework 4 finding that unrequested detail is mostly extra policy terms (windows, fees, refund timing) taken from the help center. These are raw flags on a non-random subset, so they are not a failure rate; they show where a reviewer should read first and supply a list of traces for error analysis.

## 4. What action should happen if the estimate crosses the threshold?

Treat a crossing as a trigger for human review, not as a confirmed regression. Read the flagged traces from that period, starting with the risk group, and confirm which are real failures. Turn each confirmed failure into a new evaluation case in the Homework 6 suite so CI catches it from then on. With this judge, re-validate or replace it before relying on a crossing: at a 52% false-alarm rate a crossing is likely noise, so the next improvement is a sharper prompt or a stronger judge model, validated again on held-out data.
