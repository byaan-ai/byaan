# Byaan Eval Report

Model: `gpt-5.6-sol` · Engine: `codex-agentic`
Reasoning effort: `high`
Judge: `gpt-5.6-sol` via `codex`
Started: 2026-07-10T12:43:33.794630+00:00 · Finished: 2026-07-10T12:44:32.774510+00:00

Overall: **2/3** (67%)

## Pass rate by category

| Category | Passed | Total | Pass rate |
| --- | --- | --- | --- |
| base_population_anchoring | 1 | 1 | 100% |
| no_evidence_denial | 0 | 1 | 0% |
| schedule_vs_timestamp | 1 | 1 | 100% |

## Cases

| ID | Category | Type | Pass | Answer | SQL | Judge | Retry | Error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bp-001 | base_population_anchoring | numeric | ✅ | ✅ | - | - | 0 |  |
| sv-001 | schedule_vs_timestamp | numeric | ✅ | ✅ | - | ✅ | 0 |  |
| ne-001 | no_evidence_denial | refusal | ❌ | ❌ | - | ✅ | 0 |  |
