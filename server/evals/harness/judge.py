"""LLM-judge grader: a single litellm call returning {pass, reason}."""

from __future__ import annotations

import json

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


def judge_answer(rubric: str, question: str, answer: str, judge_model: str) -> dict:
    """Single temperature-0 litellm call. Returns {pass: bool, reason: str}."""
    import litellm

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": build_judge_prompt(rubric, question, answer)},
    ]
    try:
        response = litellm.completion(model=judge_model, messages=messages, temperature=0)
        content = response.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        return {"pass": False, "reason": f"judge call failed: {exc}"}
    return _parse_judge_response(content)
