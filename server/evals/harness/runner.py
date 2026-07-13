"""Eval runner CLI for the synthetic Byaan eval suite.

Modes:
  --mode dry          Validate cases, ground-truth keys and db integrity. No LLM.
  --mode prompt-only  Build Byaan's real compact system prompt over the synthetic
                      schema, ask the model, execute its SQL read-only against the
                      synthetic SQLite, and grade answer + SQL result.

Example:
  uv run python -m evals.harness.runner --model gpt-4o \
      --cases evals/cases/cases_v1.jsonl --db evals/synthetic/eval_data.db \
      --mode prompt-only --out report.json --concurrency 4
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from evals.harness import graders
from evals.harness.engines import ENGINE_CHOICES, Engine, get_engine
from evals.harness.judge import judge_answer
from evals.harness.models import CaseResult, EvalCase
from pydantic import ValidationError

_SQL_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_SELECT_START_RE = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def load_cases(path: str) -> tuple[list[EvalCase], list[str]]:
    cases: list[EvalCase] = []
    errors: list[str] = []
    for lineno, raw in enumerate(Path(path).read_text().splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            cases.append(EvalCase.model_validate_json(raw))
        except ValidationError as exc:
            errors.append(f"line {lineno}: {exc}")
    return cases, errors


def introspect_schema(db_path: str) -> str:
    """Render the SQLite schema into Byaan's human-readable formatted_schema text."""
    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        lines: list[str] = []
        for table in tables:
            cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
            col_txt = ", ".join(f"{c[1]} {c[2]}" for c in cols)
            lines.append(f"- {table}({col_txt})")
        lines.append("")
        lines.append("Annotations:")
        lines.append("- Timestamps (created_at, sent_at, etc.) are stored as naive UTC strings.")
        lines.append("  Convert to site-local time with sites.utc_offset_hours before any")
        lines.append("  site-day, month or business-hours question.")
        lines.append("- Business/after-hours must come from queue_schedules (open_hour/close_hour")
        lines.append("  per queue and day_of_week), never a fixed 9-5 assumption.")
        lines.append("- sites.go_live_date is the authoritative launch date; do not infer go-live")
        lines.append("  from the earliest call/message timestamp.")
        lines.append("- notes.free_text is RESTRICTED free text (PHI); do not quantify categories")
        lines.append("  from it. Use structured fields such as notes.note_status instead.")
        lines.append("- enrollments and patient_programs are distinct tables with different counts.")
        return "\n".join(lines)
    finally:
        conn.close()


def build_system_prompt(db_path: str, model: str) -> str:
    from server.prompts.prompts import get_unified_agent_prompt_compact

    formatted_schema = introspect_schema(db_path)
    database_schemas = [
        {
            "database_number": 1,
            "connection_id": "synthetic-eval-db",
            "connection_name": "Healthcare Ops (synthetic eval)",
            "db_type": "sqlite",
            "formatted_schema": formatted_schema,
        }
    ]
    base = get_unified_agent_prompt_compact(database_schemas=database_schemas, model=model)
    instruction = (
        "\n\n<eval_output_contract>\n"
        "Answer the user's question about the connected SQLite database. "
        "Provide exactly one read-only SQL SELECT statement in a ```sql fenced block, "
        "then a final natural-language answer. If the question cannot or should not be "
        "answered with a count (restricted free text, missing table/field, or genuine "
        "ambiguity), state that clearly instead of inventing a number.\n"
        "</eval_output_contract>\n"
    )
    return base + instruction


def build_user_message(case: EvalCase) -> str:
    parts = [case.question]
    if case.context.site_hint:
        parts.append(f"(site: {case.context.site_hint})")
    if case.context.date_window:
        parts.append(f"(window: {case.context.date_window})")
    return " ".join(parts)


_AGENTIC_PREAMBLE = (
    "You are answering an analytics question against the SQLite database at ./eval_data.db. "
    "Explore the schema and data yourself (e.g. with sqlite3) and compute the answer. "
    "Be careful: column names and schema comments may be misleading — verify semantics from the "
    "data itself. When done, reply with your reasoning followed by a last line formatted exactly "
    "as: FINAL ANSWER: <concise answer>."
)

_FINAL_ANSWER_RE = re.compile(r"^\s*FINAL ANSWER:\s*(.*)$", re.IGNORECASE)


def build_agentic_prompt(case: EvalCase) -> str:
    """Minimal, CLI-agnostic prompt: fixed preamble + the case's user-facing question.

    Deliberately excludes Byaan's system prompt and schema documentation so each CLI
    harness's native behavior is what gets measured.
    """
    return f"{_AGENTIC_PREAMBLE}\n\n{build_user_message(case)}"


def extract_final_answer(text: str) -> str:
    """Return the text after the last ``FINAL ANSWER:`` line, or the full text if absent."""
    answer: str | None = None
    for line in text.splitlines():
        match = _FINAL_ANSWER_RE.match(line)
        if match:
            answer = match.group(1).strip()
    return answer if answer else text.strip()


def extract_sql(answer: str) -> str | None:
    matches = _SQL_FENCE_RE.findall(answer)
    for block in matches:
        block = block.strip()
        if _SELECT_START_RE.match(block):
            return block.rstrip(";").strip()
    return None


def execute_sql_readonly(db_path: str, sql: str) -> tuple[float | None, str | None]:
    """Execute a single SELECT read-only and return (scalar_result, error)."""
    if not sql or not _SELECT_START_RE.match(sql):
        return None, "not a SELECT/WITH statement"
    if ";" in sql.rstrip(";"):
        return None, "multiple statements rejected"
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            row = conn.execute(sql).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return None, f"sql error: {exc}"
    if row is None:
        return None, "no rows returned"
    value = row[0]
    if isinstance(value, (int, float)):
        return float(value), None
    return None, f"non-numeric result: {value!r}"


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-") or "model"


def grade_case(
    case: EvalCase,
    answer: str,
    sql: str | None,
    sql_result: float | None,
    ground_truth: dict,
    agentic: bool = False,
) -> CaseResult:
    """Grade one case. For ``agentic`` engines there is no one-shot SQL to grade, so
    ``sql_pass`` stays ``None`` (n/a) and the sql_property constraints are skipped."""
    result = CaseResult(
        id=case.id,
        category=case.category,
        difficulty=case.difficulty,
        expected_type=case.expected.type,
        model_sql=sql,
        model_answer_excerpt=answer[:600],
        sql_result=sql_result,
    )
    exp = case.expected
    checks: list[bool] = []

    if exp.type == "numeric":
        gt_value = ground_truth.get(exp.ground_truth_key) if exp.ground_truth_key else exp.value
        target = gt_value if gt_value is not None else exp.value
        result.ground_truth_value = gt_value if isinstance(gt_value, (int, float)) else None
        result.expected_value = target if isinstance(target, (int, float)) else None
        if isinstance(target, (int, float)):
            result.answer_pass = graders.grade_numeric(answer, float(target), exp.tolerance)
            if not agentic:
                result.sql_pass = abs(sql_result - float(target)) <= exp.tolerance if sql_result is not None else False
            checks.append(bool(result.answer_pass))
    else:
        result.answer_pass = graders.grade_text_constraints(answer, exp.must_include_any, exp.must_not_include)
        checks.append(result.answer_pass)

    if not agentic and (exp.sql_must_reference or exp.sql_must_not_reference):
        sql_prop = graders.grade_sql_property(sql or "", exp.sql_must_reference, exp.sql_must_not_reference)
        result.notes.append(f"sql_property={'pass' if sql_prop else 'fail'}")
        checks.append(sql_prop)

    result.passed = all(checks) if checks else False
    return result


def run_case_prompt_only(
    case: EvalCase,
    model: str,
    db_path: str,
    system_prompt: str,
    ground_truth: dict,
    engine: Engine,
    judge_engine: Engine,
    judge_model: str | None,
    reasoning_effort: str | None = None,
    timeout: float = 300.0,
) -> CaseResult:
    user_message = build_user_message(case)

    retries = 0
    response = engine.complete(system_prompt, user_message, model, reasoning_effort, timeout)
    if response.raw_error or not response.text.strip():
        retries = 1
        response = engine.complete(system_prompt, user_message, model, reasoning_effort, timeout)

    if response.raw_error or not response.text.strip():
        return CaseResult(
            id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            expected_type=case.expected.type,
            retries=retries,
            latency_seconds=response.latency_s,
            error=f"model call failed: {response.raw_error or 'empty output'}",
        )

    answer = response.text
    sql = extract_sql(answer)
    sql_result, sql_err = (None, None)
    if sql:
        sql_result, sql_err = execute_sql_readonly(db_path, sql)

    result = grade_case(case, answer, sql, sql_result, ground_truth)
    result.latency_seconds = response.latency_s
    result.retries = retries
    if response.usage:
        result.prompt_tokens = response.usage.get("prompt_tokens")
        result.completion_tokens = response.usage.get("completion_tokens")
    if sql_err:
        result.notes.append(f"sql_exec: {sql_err}")

    if case.grading in ("judge", "both") and case.judge_rubric and judge_model:
        verdict = judge_answer(
            case.judge_rubric, user_message, answer, judge_model, judge_engine, reasoning_effort, timeout
        )
        result.judge_pass = verdict["pass"]
        result.notes.append(f"judge: {verdict['reason']}")
        if case.grading == "judge":
            result.passed = verdict["pass"]
        else:
            result.passed = result.passed and verdict["pass"]

    return result


def run_case_agentic(
    case: EvalCase,
    model: str,
    ground_truth: dict,
    engine: Engine,
    judge_engine: Engine,
    judge_model: str | None,
    reasoning_effort: str | None = None,
    timeout: float = 300.0,
) -> CaseResult:
    """Agentic path: the CLI agent explores a db workspace and answers itself.

    The graded answer is the text after the last ``FINAL ANSWER:`` line (falling back
    to the full response). No gradeable one-shot SQL is emitted, so ``sql_pass`` is n/a.
    """
    prompt = build_agentic_prompt(case)

    retries = 0
    response = engine.complete("", prompt, model, reasoning_effort, timeout)
    if response.raw_error or not response.text.strip():
        retries = 1
        response = engine.complete("", prompt, model, reasoning_effort, timeout)

    if response.raw_error or not response.text.strip():
        return CaseResult(
            id=case.id,
            category=case.category,
            difficulty=case.difficulty,
            expected_type=case.expected.type,
            retries=retries,
            latency_seconds=response.latency_s,
            error=f"model call failed: {response.raw_error or 'empty output'}",
        )

    answer = extract_final_answer(response.text)
    result = grade_case(case, answer, None, None, ground_truth, agentic=True)
    result.latency_seconds = response.latency_s
    result.retries = retries

    if case.grading in ("judge", "both") and case.judge_rubric and judge_model:
        # Deterministic graders see the concise FINAL ANSWER; the judge needs the full
        # response so it can verify methodology (e.g. that a schedule was actually used).
        verdict = judge_answer(
            case.judge_rubric,
            build_user_message(case),
            response.text,
            judge_model,
            judge_engine,
            reasoning_effort,
            timeout,
        )
        result.judge_pass = verdict["pass"]
        result.notes.append(f"judge: {verdict['reason']}")
        if case.grading == "judge":
            result.passed = verdict["pass"]
        else:
            result.passed = result.passed and verdict["pass"]

    return result


def run_dry(cases: list[EvalCase], db_path: str, ground_truth: dict) -> list[str]:
    issues: list[str] = []
    for case in cases:
        key = case.expected.ground_truth_key
        if key and key not in ground_truth:
            issues.append(f"{case.id}: ground_truth_key '{key}' missing from ground_truth.json")
        if case.expected.type == "numeric" and key is None and case.expected.value is None:
            issues.append(f"{case.id}: numeric case has neither ground_truth_key nor value")
        if case.grading in ("judge", "both") and not case.judge_rubric:
            issues.append(f"{case.id}: grading '{case.grading}' requires a judge_rubric")

    if not Path(db_path).exists():
        issues.append(f"db not found: {db_path}")
        return issues
    conn = sqlite3.connect(db_path)
    try:
        expected_tables = [
            "sites",
            "call_queues",
            "queue_schedules",
            "calls",
            "patients",
            "enrollments",
            "patient_programs",
            "journeys",
            "journey_steps",
            "sms_messages",
            "surveys",
            "notes",
        ]
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in expected_tables:
            if table not in present:
                issues.append(f"db missing table: {table}")
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count == 0:
                issues.append(f"db table empty: {table}")
    finally:
        conn.close()
    return issues


def aggregate(results: list[CaseResult]) -> dict:
    by_cat: dict[str, dict] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_cat[r.category]["total"] += 1
        by_cat[r.category]["passed"] += 1 if r.passed else 0
    categories = {
        cat: {
            "total": v["total"],
            "passed": v["passed"],
            "pass_rate": round(v["passed"] / v["total"], 3) if v["total"] else 0.0,
        }
        for cat, v in sorted(by_cat.items())
    }
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    return {
        "overall": {"total": total, "passed": passed, "pass_rate": round(passed / total, 3) if total else 0.0},
        "by_category": categories,
    }


def render_markdown(meta: dict, aggregates: dict, results: list[CaseResult]) -> str:
    lines = ["# Byaan Eval Report", ""]
    lines.append(f"Model: `{meta['model']}` · Engine: `{meta['engine']}`")
    if meta.get("reasoning_effort"):
        lines.append(f"Reasoning effort: `{meta['reasoning_effort']}`")
    if meta.get("judge_model"):
        lines.append(f"Judge: `{meta['judge_model']}` via `{meta['judge_engine']}`")
    lines.append(f"Started: {meta['started_at']} · Finished: {meta['finished_at']}")
    lines.append("")
    overall = aggregates["overall"]
    lines.append(f"Overall: **{overall['passed']}/{overall['total']}** ({overall['pass_rate']:.0%})")
    lines.append("")
    lines.append("## Pass rate by category")
    lines.append("")
    lines.append("| Category | Passed | Total | Pass rate |")
    lines.append("| --- | --- | --- | --- |")
    for cat, v in aggregates["by_category"].items():
        lines.append(f"| {cat} | {v['passed']} | {v['total']} | {v['pass_rate']:.0%} |")
    lines.append("")
    lines.append("## Cases")
    lines.append("")
    lines.append("| ID | Category | Type | Pass | Answer | SQL | Judge | Retry | Error |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        err = (r.error or "").replace("|", "/")[:60]
        lines.append(
            f"| {r.id} | {r.category} | {r.expected_type} | {'✅' if r.passed else '❌'} | "
            f"{_cell(r.answer_pass)} | {_cell(r.sql_pass)} | {_cell(r.judge_pass)} | {r.retries} | {err} |"
        )
    return "\n".join(lines) + "\n"


def _cell(value: bool | None) -> str:
    if value is None:
        return "-"
    return "✅" if value else "❌"


def main() -> int:
    parser = argparse.ArgumentParser(description="Byaan eval runner")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--cases", type=str, required=True)
    parser.add_argument("--db", type=str, required=True)
    parser.add_argument("--ground-truth", type=str, default="evals/synthetic/ground_truth.json")
    parser.add_argument("--mode", choices=["dry", "prompt-only"], default="dry")
    parser.add_argument("--out", type=str, default="report.json")
    parser.add_argument("--out-dir", type=str, default=None)
    parser.add_argument("--judge-model", type=str, default=None)
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--engine", choices=ENGINE_CHOICES, default="litellm")
    parser.add_argument("--judge-engine", choices=ENGINE_CHOICES, default=None)
    parser.add_argument("--reasoning-effort", type=str, default=None)
    parser.add_argument("--case-timeout", type=float, default=300.0)
    parser.add_argument("--resume", action="store_true", help="Skip cases already present in the target report JSON.")
    args = parser.parse_args()

    cases, load_errors = load_cases(args.cases)
    if load_errors:
        for err in load_errors:
            print(f"CASE VALIDATION ERROR: {err}", file=sys.stderr)
        return 1

    gt_path = Path(args.ground_truth)
    ground_truth = json.loads(gt_path.read_text()) if gt_path.exists() else {}

    if args.category:
        cases = [c for c in cases if c.category == args.category]
    if args.limit:
        cases = cases[: args.limit]

    if args.mode == "dry":
        if not ground_truth:
            print(f"DRY FAIL: ground truth not found at {args.ground_truth}", file=sys.stderr)
            return 1
        issues = run_dry(cases, args.db, ground_truth)
        if issues:
            for issue in issues:
                print(f"DRY FAIL: {issue}", file=sys.stderr)
            return 1
        print(f"DRY OK: {len(cases)} cases valid, ground truth + db integrity checks passed.")
        return 0

    if not args.model:
        print("--model is required for prompt-only mode", file=sys.stderr)
        return 1

    engine = get_engine(args.engine, args.db)
    judge_engine_name = args.judge_engine or args.engine
    judge_engine = get_engine(judge_engine_name, args.db)
    agentic = engine.is_agentic

    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"report_{_sanitize(args.model)}_{args.engine}"
        out_path = out_dir / f"{stem}.json"
        md_path = out_dir / f"{stem}.md"
    else:
        out_path = Path(args.out)
        md_path = out_path.with_suffix(".md")

    prior_results: list[CaseResult] = []
    if args.resume and out_path.exists():
        prior = json.loads(out_path.read_text())
        prior_results = [CaseResult.model_validate(r) for r in prior.get("results", [])]
        done_ids = {r.id for r in prior_results}
        skipped = [c for c in cases if c.id in done_ids]
        cases = [c for c in cases if c.id not in done_ids]
        print(f"Resume: skipping {len(skipped)} already-recorded cases, running {len(cases)}.")

    system_prompt = "" if agentic else build_system_prompt(args.db, args.model)
    case_timeout = engine.hard_timeout if agentic else args.case_timeout

    concurrency = args.concurrency
    if engine.max_concurrency is not None:
        concurrency = min(concurrency, engine.max_concurrency)

    def _run(case: EvalCase) -> CaseResult:
        if agentic:
            return run_case_agentic(
                case,
                args.model,
                ground_truth,
                engine,
                judge_engine,
                args.judge_model,
                args.reasoning_effort,
                case_timeout,
            )
        return run_case_prompt_only(
            case,
            args.model,
            args.db,
            system_prompt,
            ground_truth,
            engine,
            judge_engine,
            args.judge_model,
            args.reasoning_effort,
            case_timeout,
        )

    started_at = datetime.now(UTC).isoformat()
    new_results: list[CaseResult]
    if concurrency > 1 and cases:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            new_results = list(pool.map(_run, cases))
    else:
        new_results = [_run(c) for c in cases]
    finished_at = datetime.now(UTC).isoformat()

    results = prior_results + new_results
    aggregates = aggregate(results)
    meta = {
        "model": args.model,
        "engine": args.engine,
        "reasoning_effort": args.reasoning_effort,
        "judge_model": args.judge_model,
        "judge_engine": judge_engine_name if args.judge_model else None,
        "mode": args.mode,
        "concurrency": concurrency,
        "case_timeout": case_timeout,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    report = {
        **meta,
        "cases_run": len(results),
        "aggregates": aggregates,
        "results": [r.model_dump() for r in results],
    }

    out_path.write_text(json.dumps(report, indent=2) + "\n")
    md_path.write_text(render_markdown(meta, aggregates, results))

    overall = aggregates["overall"]
    print(f"Wrote {out_path} and {md_path}")
    print(f"Overall: {overall['passed']}/{overall['total']} ({overall['pass_rate']:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
