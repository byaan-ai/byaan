# Byaan Synthetic Eval Suite

A self-contained eval harness that measures whether an LLM, given Byaan's **real**
compact system prompt, answers BI questions with the discipline the product
requires: anchoring the right base population, honoring exact date windows in
site-local time, scoping to the right site, deriving business/after-hours from
schedules, refusing to quantify restricted free text, using recorded go-live
dates, denying when there is no evidence, and stating definitions.

Everything runs against a **seeded synthetic SQLite database** in a neutral
healthcare-ops domain (sites, call_queues, queue_schedules, calls, patients,
enrollments, patient_programs, journeys, journey_steps, sms_messages, surveys,
notes). No customer data is involved.

## Layout

```
evals/
├── synthetic/
│   ├── generate_data.py     # seeded generator + ground-truth computation
│   ├── eval_data.db         # generated SQLite db (regenerate any time)
│   └── ground_truth.json    # generated exact expected numbers, keyed by case
├── cases/
│   └── cases_v1.jsonl       # 65 eval cases across 10 categories
└── harness/
    ├── models.py            # pydantic case + result models
    ├── graders.py           # deterministic graders (numeric/text/sql_property)
    ├── judge.py             # optional LLM-judge grader
    └── runner.py            # CLI: dry + prompt-only modes
```

All commands run from `server/` via `uv run`.

## 1. Generate the data

Deterministic: the same `--seed` always yields identical rows and an identical
`ground_truth.json` (fixed base date 2026-01-05, `random.Random(seed)`, no
`datetime.now()`).

```bash
uv run python -m evals.synthetic.generate_data --seed 42 \
    --db evals/synthetic/eval_data.db \
    --ground-truth evals/synthetic/ground_truth.json
```

The generator also embeds the traps the eval probes:
- timestamps stored naive-UTC with a per-site `utc_offset_hours` (tz slips at
  month boundaries),
- `enrollments` vs `patient_programs` counts diverge by ~4%,
- Harbor Point Health's data starts in mid-March though its `go_live_date` is
  Feb 1 (audit layer newer than the feature),
- the Weekend Overflow queue is created mid-window (Apr 1),
- `notes.free_text` is a restricted column.

## 2. Dry mode (free, no LLM — use in CI)

Validates every case against the pydantic schema, checks each
`ground_truth_key` exists, and checks db integrity (tables present and
non-empty). Exits non-zero on any problem.

```bash
uv run python -m evals.harness.runner --mode dry \
    --cases evals/cases/cases_v1.jsonl \
    --db evals/synthetic/eval_data.db \
    --ground-truth evals/synthetic/ground_truth.json
```

## 3. Run against a model (prompt-only mode)

Builds Byaan's real compact system prompt over the synthetic schema, asks the
model each question, extracts the fenced SQL, executes it **read-only** against
the synthetic SQLite (non-`SELECT` statements are rejected), and grades both the
natural-language answer and the executed-SQL result.

```bash
uv run python -m evals.harness.runner --model gpt-4o \
    --cases evals/cases/cases_v1.jsonl \
    --db evals/synthetic/eval_data.db \
    --mode prompt-only --out report.json \
    --judge-model gpt-4o --concurrency 4
```

Useful flags: `--category date_window_fidelity` (run one category),
`--limit 10` (first N cases), `--concurrency N` (parallel model calls),
`--judge-model` (enables the LLM judge for `judge`/`both` cases).

`--model` / `--judge-model` are passed straight to litellm, so any
litellm-supported id works (`gpt-4o`, `azure/...`, `bedrock/...`, etc.), with
credentials supplied via the usual litellm environment variables.

Outputs `report.json` (per-case results + aggregates) and a sibling
`report.md` summary table.

## 3b. Engines — benchmark locally-authenticated CLIs (no API keys)

The model call and the judge call each route through an **engine**. Pick with
`--engine` (and optionally `--judge-engine`, defaulting to `--engine`):

| Engine | Backend | Auth |
| --- | --- | --- |
| `litellm` (default) | `litellm.completion` | API keys / env vars |
| `codex` | OpenAI Codex CLI (`codex exec`) | local Codex login, bills the OpenAI plan |
| `claude-cli` | Claude Code CLI (`claude -p`) | local Claude login, bills the Claude plan |
| `claude-agentic` | Claude Code CLI with a db workspace + Bash | local Claude login |
| `codex-agentic` | Codex CLI with a db workspace under a read-only sandbox | local Codex login |

CLI engines run each call as a subprocess with `cwd=/tmp` (so no project
`CLAUDE.md`/MCP context leaks in) and a hard `--case-timeout` (default 300s,
killed on expiry). Effective concurrency for CLI engines is capped at 3 even if
`--concurrency` is higher. Each case gets **1 retry** on empty/errored output,
recorded per-case as `retries`/`error`.

Isolation flags used:
- **codex**: `codex exec --model <m> [-c model_reasoning_effort=<effort>]
  --sandbox read-only --skip-git-repo-check -o <tmpfile> "<system+user prompt>"`.
  Codex has no system-prompt flag, so system + user are combined with a clear
  separator; the final assistant message is read back from the unique tmpfile.
- **claude-cli**: `claude -p --model <m> --system-prompt <sys> --strict-mcp-config
  --mcp-config '{"mcpServers":{}}' --setting-sources '' [--effort <effort>]
  --disallowedTools Bash Edit Write Read Glob Grep WebFetch WebSearch NotebookEdit Task`.
  The user prompt is delivered on **stdin**; the reply is read from stdout.

```bash
# Codex (gpt-5.6-sol), low reasoning, deterministic-only cases
uv run python -m evals.harness.runner --model gpt-5.6-sol \
    --engine codex --reasoning-effort low \
    --cases evals/cases/cases_v1.jsonl --db evals/synthetic/eval_data.db \
    --mode prompt-only --category sql_correctness --limit 1

# Claude Code CLI (claude-opus-4-8)
uv run python -m evals.harness.runner --model claude-opus-4-8 \
    --engine claude-cli \
    --cases evals/cases/cases_v1.jsonl --db evals/synthetic/eval_data.db \
    --mode prompt-only --out-dir reports/
```

`--reasoning-effort` accepts `low|medium|high|xhigh` (mapped to
`model_reasoning_effort` for codex and `--effort` for claude-cli; passed as
`reasoning_effort` to litellm when supported). `--out-dir DIR` writes
`report_{model}_{engine}.json/.md` into `DIR` (leaving `--out` behavior intact).
The report records engine, model, reasoning_effort, judge engine/model, start/finish
timestamps, and per-case retry/error info.

### Agentic engines — measure the model-in-CLI-harness ceiling

`claude-agentic` and `codex-agentic` do **not** run pure text completions. Each
case gets a private temp workspace containing a copy of the db as `eval_data.db`,
and the CLI agent answers by exploring/querying it with its own tools (Claude with
only the Bash tool enabled; Codex under a `read-only` sandbox). The prompt is
minimal and identical for both CLIs — no Byaan system prompt or schema docs — so
each harness's native behavior is what gets measured. The graded answer is the
text after the last `FINAL ANSWER:` line (fallback: the full response); the LLM
judge still sees the full reasoning. These engines emit no gradeable one-shot SQL,
so `sql_pass` is `n/a`. Hard per-case timeouts are 300s (claude) / 420s (codex),
concurrency capped at 4. Supply a non-agentic `--judge-engine` (e.g. `codex`).

`--resume` skips cases already present in the target report JSON, runs only the
rest, and merges them back in — safe to re-invoke after a partial/interrupted run.

## Interpreting the report

- **overall / by_category pass_rate** — the headline. Each category maps to a
  known Byaan failure mode.
- Per case: `answer_pass` (the number/refusal in the prose), `sql_pass` (the
  executed SQL result vs ground truth, recorded separately), `judge_pass` (if a
  judge model was supplied), and any `sql_property` note.
- Numeric answers are graded with `tolerance` (0 for counts, small for averages)
  and ground truths are deliberately non-round so guessing fails.

SKILL-based prompt improvements (query-discipline rules, casebook lessons)
should visibly move category pass-rates — e.g. adding the after-hours skill
should raise `schedule_vs_timestamp`, and the restricted-text rule should raise
`restricted_free_text_refusal`. Track category pass-rate deltas across runs.

## Adding cases

Append a JSON line to `cases/cases_v1.jsonl` following the `EvalCase` schema in
`harness/models.py`:

- `expected.type`: `numeric` | `refusal` | `clarification` | `definition_stated`
  | `sql_property`.
- Numeric cases point at a `ground_truth_key` computed by the generator — add
  the computation in `compute_ground_truth` and regenerate.
- Text cases use `must_include_any` / `must_not_include` (case-insensitive).
- Optionally constrain the SQL with `sql_must_reference` /
  `sql_must_not_reference`.
- Set `grading` to `deterministic`, `judge`, or `both`, and supply a
  `judge_rubric` whenever `judge`/`both` is used.

Then re-run dry mode; it fails fast on dangling keys or missing rubrics.
