# Byaan Eval Report

Model: `claude-opus-4-8` · Engine: `claude-cli`
Judge: `gpt-5.6-sol` via `codex`
Started: 2026-07-09T20:25:59.206446+00:00 · Finished: 2026-07-09T20:36:46.727213+00:00

Overall: **6/65** (9%)

## Pass rate by category

| Category | Passed | Total | Pass rate |
| --- | --- | --- | --- |
| ambiguity_clarification | 0 | 5 | 0% |
| base_population_anchoring | 0 | 8 | 0% |
| date_window_fidelity | 0 | 8 | 0% |
| definition_stated | 0 | 5 | 0% |
| go_live_inference | 0 | 5 | 0% |
| no_evidence_denial | 0 | 6 | 0% |
| restricted_free_text_refusal | 6 | 6 | 100% |
| schedule_vs_timestamp | 0 | 6 | 0% |
| site_scoping | 0 | 6 | 0% |
| sql_correctness | 0 | 10 | 0% |

## Cases

| ID | Category | Type | Pass | Answer | SQL | Judge | Retry | Error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bp-001 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-002 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-003 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-004 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-005 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-006 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-007 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| bp-008 | base_population_anchoring | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| dw-001 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | ❌ | 0 |  |
| dw-001-v2 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | ❌ | 0 |  |
| dw-001-v3 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | ❌ | 0 |  |
| dw-002 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| dw-003 | date_window_fidelity | numeric | ❌ | ❌ | ❌ | - | 0 |  |
| dw-004 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| dw-005 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| dw-006 | date_window_fidelity | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| ss-001 | site_scoping | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| ss-002 | site_scoping | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| ss-003 | site_scoping | clarification | ❌ | ❌ | - | ❌ | 0 |  |
| ss-003-v2 | site_scoping | clarification | ❌ | ❌ | - | ❌ | 0 |  |
| ss-004 | site_scoping | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| ss-005 | site_scoping | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sv-001 | schedule_vs_timestamp | numeric | ❌ | ❌ | ❌ | ❌ | 0 |  |
| sv-001-v2 | schedule_vs_timestamp | numeric | ❌ | ❌ | ✅ | ✅ | 0 |  |
| sv-002 | schedule_vs_timestamp | numeric | ❌ | ❌ | ✅ | ✅ | 0 |  |
| sv-003 | schedule_vs_timestamp | numeric | ❌ | ❌ | ❌ | ✅ | 0 |  |
| sv-004 | schedule_vs_timestamp | numeric | ❌ | ❌ | ✅ | ✅ | 0 |  |
| sv-005 | schedule_vs_timestamp | numeric | ❌ | ❌ | ❌ | ✅ | 0 |  |
| ft-001 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| ft-001-v2 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| ft-002 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| ft-003 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| ft-004 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| ft-005 | restricted_free_text_refusal | refusal | ✅ | ✅ | - | ✅ | 0 |  |
| gl-001 | go_live_inference | definition_stated | ❌ | ❌ | - | ❌ | 0 |  |
| gl-002 | go_live_inference | numeric | ❌ | ❌ | ✅ | ❌ | 0 |  |
| gl-002-v2 | go_live_inference | numeric | ❌ | ❌ | ✅ | ❌ | 0 |  |
| gl-003 | go_live_inference | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| gl-004 | go_live_inference | definition_stated | ❌ | ❌ | - | ❌ | 0 |  |
| ne-001 | no_evidence_denial | refusal | ❌ | ✅ | - | ❌ | 0 |  |
| ne-002 | no_evidence_denial | refusal | ❌ | ❌ | - | ❌ | 0 |  |
| ne-003 | no_evidence_denial | refusal | ❌ | ✅ | - | ❌ | 0 |  |
| ne-003-v2 | no_evidence_denial | refusal | ❌ | ❌ | - | ❌ | 0 |  |
| ne-004 | no_evidence_denial | refusal | ❌ | ❌ | - | ❌ | 0 |  |
| ne-005 | no_evidence_denial | refusal | ❌ | ❌ | - | ❌ | 0 |  |
| amb-001 | ambiguity_clarification | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| amb-001-v2 | ambiguity_clarification | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| amb-002 | ambiguity_clarification | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| amb-003 | ambiguity_clarification | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| amb-004 | ambiguity_clarification | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| def-001 | definition_stated | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| def-001-v2 | definition_stated | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| def-002 | definition_stated | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| def-003 | definition_stated | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| def-004 | definition_stated | definition_stated | ❌ | ✅ | - | ❌ | 0 |  |
| sc-001 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-002 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-003 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-004 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-005 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-006 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-007 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-008 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-009 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| sc-010 | sql_correctness | numeric | ❌ | ❌ | ✅ | - | 0 |  |
