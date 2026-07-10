"""Pydantic models for eval cases and results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ExpectedType = Literal["numeric", "refusal", "clarification", "definition_stated", "sql_property"]
Difficulty = Literal["easy", "medium", "hard"]
Grading = Literal["deterministic", "judge", "both"]


class CaseContext(BaseModel):
    site_hint: str | None = None
    date_window: str | None = None


class CaseExpected(BaseModel):
    type: ExpectedType
    value: float | None = None
    tolerance: float = 0
    ground_truth_key: str | None = None
    must_include_any: list[str] | None = None
    must_not_include: list[str] | None = None
    sql_must_reference: list[str] | None = None
    sql_must_not_reference: list[str] | None = None


class EvalCase(BaseModel):
    id: str
    category: str
    difficulty: Difficulty
    question: str
    context: CaseContext = Field(default_factory=CaseContext)
    expected: CaseExpected
    grading: Grading
    judge_rubric: str | None = None


class CaseResult(BaseModel):
    id: str
    category: str
    difficulty: Difficulty
    expected_type: ExpectedType
    answer_pass: bool | None = None
    sql_pass: bool | None = None
    judge_pass: bool | None = None
    passed: bool = False
    model_sql: str | None = None
    model_answer_excerpt: str | None = None
    sql_result: float | None = None
    expected_value: float | None = None
    ground_truth_value: float | None = None
    latency_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retries: int = 0
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
