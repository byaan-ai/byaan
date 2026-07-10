"""Deterministic graders for eval answers and SQL.

All graders are pure and side-effect free so they are trivially testable
without any LLM or database access.
"""

from __future__ import annotations

import re

_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """Extract numeric values from free text, robust to $, commas and %.

    "$1,234.5" -> 1234.5, "45%" -> 45.0. Bare years/ids are still returned;
    numeric grading tolerates that by matching any extracted value.
    """
    if not text:
        return []
    numbers: list[float] = []
    for raw in _NUMBER_RE.findall(text):
        cleaned = raw.replace(",", "")
        try:
            numbers.append(float(cleaned))
        except ValueError:
            continue
    return numbers


def grade_numeric(answer: str, expected: float, tolerance: float = 0) -> bool:
    """Pass if any number in the answer is within tolerance of expected."""
    if expected is None:
        return False
    for n in extract_numbers(answer):
        if abs(n - expected) <= tolerance:
            return True
    return False


def _contains_any(text: str, needles: list[str] | None) -> bool:
    if not needles:
        return True
    low = text.lower()
    return any(n.lower() in low for n in needles)


def _contains_none(text: str, needles: list[str] | None) -> bool:
    if not needles:
        return True
    low = text.lower()
    return all(n.lower() not in low for n in needles)


def grade_text_constraints(
    answer: str,
    must_include_any: list[str] | None = None,
    must_not_include: list[str] | None = None,
) -> bool:
    """Case-insensitive must_include_any / must_not_include check.

    Used for refusal, clarification and definition_stated expected types.
    """
    return _contains_any(answer, must_include_any) and _contains_none(answer, must_not_include)


def _references_table(sql: str, table: str) -> bool:
    return re.search(rf"\b{re.escape(table)}\b", sql, flags=re.IGNORECASE) is not None


def grade_sql_property(
    sql: str,
    must_reference: list[str] | None = None,
    must_not_reference: list[str] | None = None,
) -> bool:
    """Regex word-boundary checks on the SQL the model produced."""
    if not sql:
        return not must_reference
    if must_reference and not all(_references_table(sql, t) for t in must_reference):
        return False
    if must_not_reference and any(_references_table(sql, t) for t in must_not_reference):
        return False
    return True
