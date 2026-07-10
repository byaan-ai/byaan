from __future__ import annotations

import json
import re

FACT_FAMILIES = ("enum", "scope", "config", "launch", "semantics", "join", "provenance")

FACT_EXTRACTION_SYSTEM = (
    "You are a precise code-fact extractor for a business-intelligence assistant. "
    "You read source files and compile the small set of facts that change how database "
    "queries are written or interpreted. You never restate obvious schema, never speculate, "
    "and every snippet you emit is copied verbatim from the provided file content. "
    "Output only the final fenced json array."
)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_last_json_array(text: str) -> list | None:
    """Parse the LAST fenced json array from LLM text, tolerating surrounding prose.

    Within each fenced block the substring from the first ``[`` to the last ``]`` is taken so
    array elements whose values contain brackets (code snippets) do not truncate the match.
    Falls back to the last bare ``[...]`` in the whole text when no fenced block parses.
    Returns None when nothing parseable is found.
    """
    if not text:
        return None

    candidates: list[str] = []
    for block in _FENCE_RE.findall(text):
        stripped = block.strip()
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start != -1 and end > start:
            candidates.append(stripped[start : end + 1])

    if not candidates:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])

    for raw in reversed(candidates):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _format_files(files: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"### FILE: {path}\n```\n{content}\n```" for path, content in files)


def build_fact_extraction_prompt(files: list[tuple[str, str]]) -> str:
    """Instruction to compile BI-relevant code facts (not summaries) from source files.

    Returns a user prompt; pair it with FACT_EXTRACTION_SYSTEM. The model must emit ONE fenced
    json array of claims, each grounded by a verbatim code snippet from the files below.
    """
    files_block = _format_files(files)

    return f"""## DATA TRUTHS EXTRACTION

Compile the facts about this codebase that change how an analyst writes or interprets database
queries. Do NOT summarize the code. Extract only claims that alter query behavior.

Classify every claim into exactly one FAMILY:
- enum — the allowed value set of a status/enum field (e.g. status only ever is 'active'|'churned').
- scope — tenancy / scoping columns that MUST filter a table or collection (e.g. every row has org_id).
- config — config-driven definitions: schedules, timezones, feature flags, cutoffs held in code.
- launch — go-live / launch constants (dates, first valid ids) that bound valid data.
- semantics — a field whose MEANING differs from its NAME (e.g. `amount` is stored in cents).
- join — the real join key / relationship between two tables or collections.
- provenance — data-source gotchas: mixed populations, soft-deletes, dedup rules, backfill artifacts.

STRICT RULES:
- Only emit a claim if it changes HOW a query is written or interpreted. Skip obvious schema.
- The `snippet` MUST be copied VERBATIM from the file content below (≤300 chars) and must prove the claim.
- One-sentence `claim`. `query_rule` states the concrete change to query writing.
- If you find nothing query-changing, return an empty array.

FILES:
{files_block}

END your reply with a single fenced ```json array, and nothing after it:

```json
[
  {{
    "claim_key": "short-slug",
    "family": "enum",
    "claim": "one-sentence fact",
    "query_rule": "how this changes query writing",
    "path": "exact/file/path.py",
    "start_line": 1,
    "end_line": 1,
    "snippet": "verbatim code excerpt proving the claim (<=300 chars)"
  }}
]
```
"""
