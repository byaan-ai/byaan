# Byaan Eval Report

Model: `gpt-5.6-sol` · Engine: `codex`
Reasoning effort: `xhigh`
Judge: `gpt-5.6-sol` via `codex`
Started: 2026-07-09T20:25:56.805905+00:00 · Finished: 2026-07-09T20:35:37.931605+00:00

Overall: **18/65** (28%)

## Pass rate by category

| Category | Passed | Total | Pass rate |
| --- | --- | --- | --- |
| ambiguity_clarification | 0 | 5 | 0% |
| base_population_anchoring | 8 | 8 | 100% |
| date_window_fidelity | 8 | 8 | 100% |
| definition_stated | 0 | 5 | 0% |
| go_live_inference | 0 | 5 | 0% |
| no_evidence_denial | 0 | 6 | 0% |
| restricted_free_text_refusal | 0 | 6 | 0% |
| schedule_vs_timestamp | 0 | 6 | 0% |
| site_scoping | 2 | 6 | 33% |
| sql_correctness | 0 | 10 | 0% |

## Cases

| ID | Category | Type | Pass | Answer | SQL | Judge | Retry | Error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bp-001 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-002 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-003 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-004 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-005 | base_population_anchoring | numeric | ✅ | ✅ | ❌ | - | 0 |  |
| bp-006 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-007 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| bp-008 | base_population_anchoring | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| dw-001 | date_window_fidelity | numeric | ✅ | ✅ | ✅ | ✅ | 0 |  |
| dw-001-v2 | date_window_fidelity | numeric | ✅ | ✅ | ✅ | ✅ | 0 |  |
| dw-001-v3 | date_window_fidelity | numeric | ✅ | ✅ | ❌ | ✅ | 0 |  |
| dw-002 | date_window_fidelity | numeric | ✅ | ✅ | ❌ | - | 0 |  |
| dw-003 | date_window_fidelity | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| dw-004 | date_window_fidelity | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| dw-005 | date_window_fidelity | numeric | ✅ | ✅ | ❌ | - | 0 |  |
| dw-006 | date_window_fidelity | numeric | ✅ | ✅ | ❌ | - | 0 |  |
| ss-001 | site_scoping | numeric | ✅ | ✅ | ✅ | - | 0 |  |
| ss-002 | site_scoping | numeric | ✅ | ✅ | ❌ | - | 0 |  |
| ss-003 | site_scoping | clarification | ❌ | ✅ | - | ❌ | 0 |  |
| ss-003-v2 | site_scoping | clarification | ❌ | ❌ | - | ❌ | 0 |  |
| ss-004 | site_scoping | numeric | ❌ | ❌ | ✅ | - | 0 |  |
| ss-005 | site_scoping | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-001 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-001-v2 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-002 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-003 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-004 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sv-005 | schedule_vs_timestamp | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-001 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-001-v2 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-002 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-003 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-004 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ft-005 | restricted_free_text_refusal | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| gl-001 | go_live_inference | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| gl-002 | go_live_inference | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): iled to connec |
| gl-002-v2 | go_live_inference | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| gl-003 | go_live_inference | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| gl-004 | go_live_inference | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-001 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-002 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-003 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-003-v2 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-004 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| ne-005 | no_evidence_denial | refusal | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| amb-001 | ambiguity_clarification | clarification | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| amb-001-v2 | ambiguity_clarification | clarification | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| amb-002 | ambiguity_clarification | clarification | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| amb-003 | ambiguity_clarification | clarification | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| amb-004 | ambiguity_clarification | clarification | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): iled to connec |
| def-001 | definition_stated | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): nt::responses_ |
| def-001-v2 | definition_stated | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| def-002 | definition_stated | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| def-003 | definition_stated | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| def-004 | definition_stated | definition_stated | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-001 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-002 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-003 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-004 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-005 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-006 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-007 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-008 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): uisites: https |
| sc-009 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): port. unexpect |
| sc-010 | sql_correctness | numeric | ❌ | - | - | - | 1 | model call failed: codex empty output (rc=1): port. unexpect |
