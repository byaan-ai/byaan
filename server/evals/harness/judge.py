"""LLM-judge grader: one engine call returning {pass, reason}.

Litellm keeps temperature-0 semantics; CLI engines just complete and parse.
"""

from __future__ import annotations

import json

from evals.harness.engines import Engine

JUDGE_SYSTEM = (
    "You are a strict grader for a business-intelligence assistant. "
    "Given a rubric, the user question, and the assistant's answer, decide whether "
    "the answer satisfies the rubric. Reply with ONLY a JSON object of the form "
    '{"pass": true|false, "reason": "<one sentence>"}. No prose outside the JSON.'
)


def build_judge_prompt(rubric: str, question: str, answer: str) -> str:
    return (
        f"RUBRIC:\n{rubric}\n\n"
        f"USER QUESTION:\n{question}\n\n"
        f"ASSISTANT ANSWER:\n{answer}\n\n"
        "Does the answer satisfy the rubric?"
    )


def _parse_judge_response(content: str) -> dict:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end != -1:
        content = content[start : end + 1]
    try:
        data = json.loads(content)
        return {"pass": bool(data.get("pass")), "reason": str(data.get("reason", ""))}
    except (json.JSONDecodeError, AttributeError):
        return {"pass": False, "reason": f"unparseable judge response: {content[:200]}"}


def judge_answer(
    rubric: str,
    question: str,
    answer: str,
    judge_model: str,
    engine: Engine,
    reasoning_effort: str | None = None,
    timeout: float = 300.0,
) -> dict:
    """Single judge call through ``engine``. Returns {pass: bool, reason: str}."""
    response = engine.complete(
        system_prompt=JUDGE_SYSTEM,
        user_prompt=build_judge_prompt(rubric, question, answer),
        model=judge_model,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
    )
    if response.raw_error:
        return {"pass": False, "reason": f"judge call failed: {response.raw_error}"}
    return _parse_judge_response(response.text)
