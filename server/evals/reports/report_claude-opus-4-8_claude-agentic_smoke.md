# Byaan Eval Report

Model: `claude-opus-4-8` · Engine: `claude-agentic`
Started: 2026-07-10T12:45:05.696139+00:00 · Finished: 2026-07-10T12:45:05.696149+00:00

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
| ne-001 | no_evidence_denial | refusal | ❌ | ❌ | - | ❌ | 0 |  |
