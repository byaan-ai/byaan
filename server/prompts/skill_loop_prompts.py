from __future__ import annotations

import json
import re

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_last_json_block(text: str) -> dict | None:
    """Parse the LAST fenced json block from agent text, tolerating surrounding prose.

    Falls back to the last bare ``{...}`` object when no fenced block is present.
    Returns None when nothing parseable is found.
    """
    if not text:
        return None

    candidates = _JSON_BLOCK_RE.findall(text)
    if not candidates:
        start = text.rfind("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates = [text[start : end + 1]]

    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _format_conversation(history: list[dict[str, str]]) -> str:
    lines = []
    for turn in history:
        role = turn.get("role", "unknown").upper()
        content = (turn.get("content") or "").strip()
        lines.append(f"[{role}]\n{content}")
    return "\n\n".join(lines) if lines else "(no conversation)"


def _format_saved_queries(queries: list[dict[str, str]]) -> str:
    if not queries:
        return "(no saved queries)"
    lines = []
    for q in queries:
        name = q.get("name", "unnamed")
        query = (q.get("query") or "").strip()
        lines.append(f"### {name}\n{query}")
    return "\n\n".join(lines)


def build_verifier_prompt(history: list[dict[str, str]], saved_queries: list[dict[str, str]]) -> str:
    """Instruction for the read-only verifier agent session.

    The agent re-checks the final answer via read-only queries, treats human
    corrections in-thread as ground truth, and ends with a fenced json verdict.
    """
    conversation = _format_conversation(history)
    queries = _format_saved_queries(saved_queries)

    return f"""## CONVERSATION EVALUATION MODE (read-only verifier)

You are auditing a completed data-analysis conversation. Your job is to decide whether the
assistant's FINAL answer was correct, using only READ-ONLY queries to re-check the numbers.

STRICT RULES:
- Only run read-only queries (SELECT / find). NEVER modify data, create dashboards, or save queries.
- Treat any human correction or pushback later in the thread as GROUND TRUTH about what was wrong.
- Re-derive the key claims of the final answer independently and compare against what was said.
- If you cannot verify because the question is open-ended or evidence is missing, say so.

CONVERSATION HISTORY (chronological):
{conversation}

SAVED QUERIES IN THIS NOTEBOOK:
{queries}

After your analysis, END your reply with a single fenced ```json block, and nothing after it:

```json
{{
  "verdict": "confirmed" | "mistake" | "ambiguous",
  "summary": "one to three sentence plain-language summary of your conclusion",
  "evidence": [
    {{"claim": "what the assistant asserted", "check": "the read-only check you ran", "result": "what you found"}}
  ],
  "correction": "if verdict is mistake: the corrected fact. if ambiguous: the concrete open question to ask the user. else null"
}}
```
"""


def _format_citations(citations: list[dict]) -> str:
    if not citations:
        return "(no citations)"
    lines = []
    for c in citations:
        start = c.get("start_line")
        end = c.get("end_line")
        loc = f"{c.get('path')}:{start}-{end}" if start else str(c.get("path"))
        lines.append(
            f"- claim_key={c.get('claim_key')} | status={c.get('status')} | {loc}\n"
            f"  snippet:\n{(c.get('snippet') or '').strip()}"
        )
    return "\n".join(lines)


def _format_patches(patches: list[dict]) -> str:
    if not patches:
        return "(no diff patches available)"
    blocks = []
    for p in patches:
        patch = p.get("patch")
        body = patch if patch else "(patch omitted — file too large or binary)"
        blocks.append(f"### {p.get('filename')} (status={p.get('status')})\n```diff\n{body}\n```")
    return "\n\n".join(blocks)


def build_code_reverify_prompt(
    skill_name: str,
    skill_instructions: str,
    citations: list[dict],
    patches: list[dict],
) -> str:
    """Instruction for re-verifying a skill's code-derived claims after a commit drift.

    The agent decides, using ONLY the provided citations and diff patches, whether the skill's
    claims still hold, and proposes a single surgical edit when they do not.
    """
    citations_text = _format_citations(citations)
    patches_text = _format_patches(patches)

    return f"""## CODE DRIFT RE-VERIFICATION MODE

A skill's instructions cite specific code. Some cited files changed in a recent commit and their
snippets could no longer be located. Decide whether the skill's claims are STILL TRUE, CHANGED, or
REMOVED — using ONLY the evidence below. Do not query databases or invent facts.

SKILL: {skill_name}

CURRENT SKILL INSTRUCTIONS:
{skill_instructions.strip()}

CITED CODE (claims to re-check):
{citations_text}

RELEVANT DIFF PATCHES:
{patches_text}

For each claim, judge from the diff whether it is still-true, changed, or removed. Only propose an
edit if a claim is now wrong or stale; keep the edit minimal and grounded in the diff.

END your reply with a single fenced ```json block, and nothing after it:

```json
{{
  "still_valid": true | false,
  "summary": "one to three sentence plain-language conclusion",
  "edit": null | {{
    "section": "the skill heading/section to change",
    "before": "verbatim text being replaced",
    "after": "the replacement text",
    "proposed_instructions": "the FULL post-edit instructions text for the skill"
  }},
  "confidence": "low" | "medium" | "high"
}}
```
"""


def build_code_newfact_prompt(patches: list[dict]) -> str:
    """Instruction for probing whether a diff introduces a new query-relevant fact worth a skill.

    Cheap v1 probe over high-value changed files (models, migrations, schemas, config, constants)
    that no existing citation covers.
    """
    patches_text = _format_patches(patches)

    return f"""## NEW-FACT PROBE MODE

The following high-value files changed and are NOT covered by any existing skill citation. Decide
whether this change introduces a NEW query-relevant fact (a new enum value, scope, config flag,
constant, or launch-relevant behavior) that an analyst assistant should know. Use ONLY the diff.

DIFF PATCHES:
{patches_text}

Be conservative: only worth a new fact if it clearly changes how data should be queried or
interpreted. End your reply with a single fenced ```json block, and nothing after it:

```json
{{
  "worth_new_fact": true | false,
  "title": "short imperative title for the new fact",
  "rationale": "why this fact matters for querying/interpreting data",
  "proposed_instructions": "the FULL instructions text for a small new skill capturing the fact",
  "confidence": "low" | "medium" | "high"
}}
```
"""


def build_proposer_refuter_prompt(findings: dict, custom_skills: list[dict[str, str]]) -> str:
    """Instruction for the combined proposer + adversarial refuter session.

    Given a confirmed mistake, propose ONE surgical skill edit and then self-review it,
    only surfacing the suggestion if it survives the critique.
    """
    findings_json = json.dumps(findings, indent=2, default=str)

    if custom_skills:
        skills_text = "\n\n".join(
            f"### skill_id={s.get('id')} | name={s.get('name')}\n{(s.get('instructions') or '').strip()}"
            for s in custom_skills
        )
    else:
        skills_text = "(the tenant has no custom skills yet — a new skill may be proposed)"

    return f"""## SKILL IMPROVEMENT MODE (proposer + refuter)

A conversation was verified to contain a MISTAKE. Below is the verifier's evidence and the
tenant's existing custom skills. Do TWO things in one pass:

1. PROPOSE exactly ONE surgical change that would have prevented this mistake — either an EDIT
   to an existing skill section, or a small NEW skill. Keep it minimal and specific.
2. REFUTE your own proposal adversarially: would it overfit, contradict existing guidance, or
   cause regressions? Only let it survive if it is clearly a net improvement.

VERIFIER FINDINGS:
{findings_json}

EXISTING CUSTOM SKILLS:
{skills_text}

END your reply with a single fenced ```json block, and nothing after it:

```json
{{
  "survives": true | false,
  "skill_id": "existing skill id when suggestion_type is edit, else null",
  "skill_name": "target skill name (existing or proposed)",
  "suggestion_type": "edit" | "new_skill",
  "title": "short imperative title for the change",
  "rationale": "why this change prevents the mistake, and why it survived self-critique",
  "section": "the heading/section being edited (or the new section name)",
  "before": "verbatim text being replaced (empty string for new_skill)",
  "after": "the replacement text",
  "proposed_instructions": "the FULL post-edit instructions text for the skill",
  "confidence": "low" | "medium" | "high"
}}
```
"""
