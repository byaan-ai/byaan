# Harness Gap Analysis — Byaan one-shot vs native agentic CLIs

Date: 2026-07-10
Runs compared (all 65 cases, judge = gpt-5.6-sol via codex):

| Run | Harness | Model |
| --- | --- | --- |
| `report_claude-opus-4-8_claude-cli` | Byaan prompt, one-shot, no execution | Opus 4.8 |
| `report_claude-opus-4-8_claude-agentic` | Claude Code CLI, Bash+sqlite3, free exploration | Opus 4.8 |
| `report_gpt-5.6-sol_codex` | Byaan prompt, one-shot, no execution | gpt-5.6-sol (high) |
| `report_gpt-5.6-sol_codex-agentic` | Codex CLI, read-only sandbox, free exploration | gpt-5.6-sol (high) |

## Numeric head-to-head (fair comparison: Byaan sql_pass vs agentic answer_pass, 32 cases)

| Model | Byaan harness | Agentic CLI | Gap |
| --- | --- | --- | --- |
| Opus 4.8 | **28/32 (87%)** | 28/32 (87%) | none |
| gpt-5.6-sol high | **23/32 (71%)** | 22/32 (68%) | none (−1) |

**Headline: Byaan's harness is not the bottleneck.** Both models compute exactly as well
under Byaan's prompt as they do with free agentic exploration in their native CLIs.

## Per-category (numeric = pass/total; behavioral = judge pass over judged)

| Category | opus/byaan | opus/agentic | sol/byaan | sol/agentic |
| --- | --- | --- | --- | --- |
| base_population_anchoring | 8/8 | 8/8 | 6/8 | **8/8** |
| date_window_fidelity | **7/8** | 4/8 | **6/8** | 0/8 |
| schedule_vs_timestamp | 3/6 | **6/6** | 3/6 | **4/6** |
| site_scoping | — | — | — | — |
| sql_correctness | 10/10 | 10/10 | 8/10 | **10/10** |
| ambiguity_clarification† | j:0/5 | j:3/5 | j:2/5 | j:1/5 |
| definition_stated† | j:0/5 | j:5/5 | j:3/5 | j:5/5 |
| go_live_inference† | j:0/5 | j:4/5 | j:5/5 | j:5/5 |
| no_evidence_denial† | j:0/6 | j:0/6 | j:6/6 | j:3/6 |
| restricted_free_text_refusal† | j:6/6 | j:0/6* | j:4/6 | j:0/6* |

† Behavioral categories are NOT cross-harness comparable (see caveats). \* Structural artifact.

## Real signals

1. **Where agentic beats Byaan one-shot — schedule_vs_timestamp (Opus 3/6 → 6/6).**
   The win comes entirely from *execute-and-probe*: agents queried `queue_schedules` and the
   `utc_offset_hours` column and verified semantics from data. Production Byaan CAN execute
   queries — this gap is an artifact of the eval's one-shot mode, and simultaneously the
   strongest evidence yet that (a) a full-agent eval mode is needed to measure production
   fairly, and (b) skills encoding schedule/timezone semantics close the one-shot gap.
   Same effect on sol's base_population (6/8 → 8/8) and sql_correctness (8/10 → 10/10):
   execution feedback repairs its SQL precision deficit.

2. **Where Byaan beats agentic — date_window_fidelity (Opus 7/8 → 4/8, sol 6/8 → 0/8).**
   Byaan's prompt pins the dataset reference date and the DATE WINDOW discipline rule;
   without it, agents anchored windows wrong (off-by-boundary, raw-UTC reads). Part
   artifact (no reference date in agentic context), part genuine: dw-001 (1338 vs 1332)
   was missed by BOTH agents even with full data access. Byaan's prompt is adding real
   value here — keep the rule.

3. **Byaan production should score at least the max of both columns.** It has the prompt
   discipline AND execution. The one-shot eval understates it; the roadmap item
   "full-agent eval mode" (run cases through `stream_handoff_agent_response`) is the
   right next investment.

## Caveats (why behavioral rows are not comparable)

- **restricted_free_text_refusal**: the no-free-text policy lives in Byaan's system prompt,
  which agentic runs intentionally don't get — they analyzed the notes and answered
  honestly. Tests the policy's presence, not the model.
- **ambiguity_clarification**: the agentic prompt demands a `FINAL ANSWER:` line, which
  structurally suppresses asking clarifying questions.
- **no_evidence_denial / definition_stated**: phrase-set graders and judge disagree in both
  directions (e.g. ne-001: claude penalized for "0 — table does not exist", codex passed
  for "cannot be determined"). Grader fixes are on the backlog; treat these rows as noise.
- Judge is gpt-5.6-sol judging its own family in two runs; self-preference not controlled.

## Latency (median per case)

Byaan one-shot: opus 23s, sol 42s. Agentic: opus 24s, sol 36s (but with 4-8 tool calls each).
