# Homework 4 review summary

I reviewed Cartwheel support conversations from my Homework 3 trace export (live Langfuse, `support-*` scenarios, agent `claude-haiku-5-5`), built a failure taxonomy from my open codes, and labelled every reviewed conversation against the final modes. The review interface is in `analysis/review_app/`; the state files are in `analysis/state/`.

## Reviewed sample

I reviewed **125 distinct conversations** (one conversation = all turns of one `cartwheel.session_id`; the label is attached to the first turn's trace). Two sampled conversations (support-0149, support-0012) were never open-coded and are not counted.

| Batch | Method | Reviewed |
| --- | --- | ---: |
| `b1-uniform` | uniform random, seed 20261015 | 15 |
| `b1-cluster` | KMeans k=8 on structure only (role, turns, tool calls, tools used, reply length), nearest to centroid | 15 |
| `b2-intent` | product dimension chosen before outcomes: **intent**, stratified random, 2 to 3 per intent | 29 |
| `b3-depth` | depth searches for candidate modes plus close negatives | 25 |
| `b4-uniform` | uniform random after the taxonomy draft | 14 |
| `d-search` | Part D searches (thin modes, close negatives, search-revise-search) | 27 |

Composition: 101 shopper, 15 merchant, 9 support conversations; 78 coverage and 47 challenge scenarios; 14 damaged-record scenarios; 6 multi-turn conversations. Batch 3 and the Part D searches deliberately looked for failures, so the fractions below are **sample fractions, not prevalence estimates**.

Open coding produced 32 failure notes and 63 "no failure observed" marks in my own words, plus 49 annotations from suggestions I accepted. Of 71 AI suggestions, 53 were accepted, 3 rejected, and 15 left undecided.

**New modes in the final 15 traces:** 1 of the 14 reviewed traces in `b4-uniform` produced a previously unseen consequential mode (support-0217, `write_stacked_after_correction`: a second refund after the user corrected the item, without resolving the first). A search of all 250 conversations found no other instance, so I dropped it (see below). The other 13 fit existing modes or had no failure, so I judged the taxonomy stable.

## Final taxonomy

| Mode | Definition (short) | Requirement | Evaluator | Positives | Close negatives | Fail labels | Sample fraction | Own first-failure notes |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `policy_id_in_reply` | The reply shows an internal policy identifier (`cw-*`, `store-*-policy`). | RESP-1 (revised) | code check | 7 | 3 | 76 | 60.8% | 7 |
| `routes_around_clear_denial` | Policy and a complete record clearly deny the request, and the reply offers or opens a human review, exception, or dispute to get it anyway. | ESC-5 (new), ESC-3 | LLM judge | 21 | 2 | 21 | 16.8% | 5 |
| `unrequested_detail` | The reply adds information the user did not ask for and that is not needed to answer or explain the decision. | RESP-6 (new) | LLM judge | 11 | 1 | 17 | 13.6% | 11 |
| `refund_threshold_misrouted` | An eligible refund is not routed by the $100 threshold (above it, a ticket instead of a queued `issue_refund`; at or below, no refund issued). | ESC-1 (revised), AUTH-1, TOOL-7 | code check | 11 | 4 | 14 | 11.2% | 1 |
| `no_escalation_when_unresolved` | Policy is silent or the records conflict, and the agent answers or speculates without opening a ticket (an offer alone does not count). | ESC-3, ESC-4 | LLM judge | 6 | 4 | 9 | 7.2% | 3 |
| `bad_record_shown_to_user` | An invalid or inconsistent record value is presented, or used, as fact instead of being flagged and escalated. | RESP-3 | LLM judge | 4 | 2 | 6 | 4.8% | 1 |

Fail labels are counts over all 125 reviewed conversations (any instance, Part E). "Own first-failure notes" counts conversations where my own open-coding note was the origin. The gap between the two columns is the expected undercount of the first-failure rule: for example, `refund_threshold_misrouted` came from one note (support-0198) but the code-check search found 13 more, and `policy_id_in_reply` appears in 76 replies but was my first note in only 7.

Boundaries between neighbours:

- `routes_around_clear_denial` vs `no_escalation_when_unresolved`: a clear denial (window closed, `eligible: false`) belongs to the first; real ambiguity (policy silent, records missing or contradictory) belongs to the second. A required escalation (dispute, account change) is never a positive.
- `bad_record_shown_to_user` vs `no_escalation_when_unresolved`: asserting damaged data as fact is the first; flagging it and only offering a ticket is the second.
- `policy_id_in_reply` vs `unrequested_detail`: naming a policy by its title is allowed; only the raw identifier counts.

## Taxonomy revisions

1. **Rejected, then reinstated: `policy_id_in_reply`.** In round 1, seven of my batch-1 notes said the reply should not name policies. RESP-1 then required the identifier, so I rejected the group. In Part D I decided internal identifiers should not reach users and revised RESP-1, which made those notes describe a real requirement violation; I reinstated the mode.
2. **Merge: `offers_route_around_policy` + `unneeded_escalation` → `routes_around_clear_denial`.** My note on support-0219 ("again going around the policy") showed that one product change fixes both: when policy clearly denies a request, state it and do not route around it.
3. **Redefinition: `write_before_required_info` → `write_on_unconfirmed_order`.** I marked support-0036 clean (a refund after the eligibility check needs no condition check), which removed the support for "without required info". The mode was narrowed to a write on an order the user never identified (support-0220).
4. **Dropped: `write_on_unconfirmed_order`, `write_stacked_after_correction`, `requests_personal_data`.** Each had one positive; searches over all 250 conversations found only close negatives (for example support-0164, where the agent tells the user not to send their address in chat).

## Search for additional instances

On `routes_around_clear_denial` I ran a search-revise-search loop. Search 1 (denial signal in the tools plus route wording in the reply) returned 27 conversations. It included support-0223 and support-0224, where "not eligible" comes from a missing delivery date; I accepted 0224 as a close negative, so I revised the definition to require a complete order record. Search 2 with that condition removed the four damaged-record hits (0222, 0223, 0224, 0235). It also missed two known positives (0055, 0199) whose denial sits in the order's `refund_eligible: false` field, a gap in the retrieval rule rather than the definition.

**Rejected suggestion:** support-0150 was suggested as a close negative for `no_escalation_when_unresolved` (a dispute escalated at once). I rejected it because "it includes details that weren't explicitly asked for", which is the boundary of `unrequested_detail`: the escalation was right, but the reply was not clean. Two further rejections (support-0036, support-0231) are recorded with their boundaries in `analysis/state/suggestions.json`.

## SPEC revisions

| Change | Motivating annotations |
| --- | --- |
| RESP-1 and PURPOSE-1: base every policy claim on a retrieved policy and refer to it by title, not by internal identifier. | Batch-1 notes on support-0035, 0050, 0055, 0109, 0113, 0124, 0235 |
| New RESP-6: answer the question the user asked; do not add policy, order, or listing details the user did not ask about. | Notes on support-0008, 0172, 0205 |
| New ESC-5: when policy and a complete record clearly deny a request, state the denial; do not offer a human review, exception, or dispute to obtain it, unless a policy describes that path. | Notes on support-0199, 0219; the search-revise-search loop |
| ESC-1: an eligible refund request goes through `issue_refund` (which queues it above $100), not `escalate_to_human`. | Note on support-0198; accepted suggestion on support-0195 |

The running agent still follows its prompt ("Cite the policy id ... for every policy claim"), so the traces violate the revised RESP-1 until the prompt is changed (Homework 8).

## Comparison with AgentDebug

| My mode | Closest AgentDebug error |
| --- | --- |
| `refund_threshold_misrouted` | Planning-Action Disconnect: the agent states "over $100 is queued for review", then calls the wrong tool |
| `routes_around_clear_denial` | Constraint Ignorance |
| `no_escalation_when_unresolved` | Progress Misassessment |
| `bad_record_shown_to_user` | Outcome Misinterpretation |
| `unrequested_detail`, `policy_id_in_reply` | none: AgentDebug measures task completion, not how a support reply communicates |

AgentDebug's Inefficient Planning suggested a possible omission (redundant tool calls). My batch-1 notes on support-0051 and 0124 pointed there, but I deleted both, so no current human annotation supports it and I did not add the mode.

## Preparing for Homework 5

Only `policy_id_in_reply` has 30 Fail labels. For `unrequested_detail` (the mode I judged in Homework 5) I collected more labels with `next_to_label` and 40 targeted synthetic scenarios.

## Limitations

- **How the labels were produced.** Of the 750 Part E judgments (6 modes x 125 conversations), 108 I set one by one, 264 are evidence-backed suggestions I accepted (taxonomy positives and close negatives, and the two code checks), and 378 are Pass on my bulk confirmation after reviewing the conversations. In Homework 5 I re-checked the `unrequested_detail` labels under a sharper rule and found that many bulk Pass labels were wrong (most of the dev disagreements, and 14 of 35 test re-checks). The `unrequested_detail` fraction above (13.6%) is therefore an undercount, and the other LLM-judge modes may be undercounted for the same reason. The code-check modes are not affected.
- support-0051 and support-0098 are labelled Pass on `routes_around_clear_denial` although they match the definition as closely as support-0052 (Fail). I did not resolve that boundary.
- `no_escalation_when_unresolved` and `bad_record_shown_to_user` have 9 and 6 Fail labels; both would need synthetic scenarios before a judge could be validated.
- I skipped the optional Raindrop Workshop part (Part C).
