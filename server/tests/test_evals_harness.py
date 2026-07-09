"""Tests for the synthetic eval harness. No LLM calls, no network.

Run: cd server && PYTHONPATH=..:tests uv run pytest tests/test_evals_harness.py -q
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from evals.harness import graders, runner
from evals.harness.models import EvalCase
from evals.synthetic import generate_data

SERVER_DIR = Path(__file__).resolve().parent.parent
CASES_PATH = SERVER_DIR / "evals" / "cases" / "cases_v1.jsonl"


def _build_db(tmp_path: Path, seed: int = 42) -> tuple[Path, dict]:
    import sqlite3

    rng = random.Random(seed)
    data = generate_data.generate(rng)
    gt = generate_data.compute_ground_truth(data)
    db_path = tmp_path / "eval.db"
    conn = sqlite3.connect(str(db_path))
    try:
        generate_data._insert(conn, data)
    finally:
        conn.close()
    return db_path, gt


def test_generator_determinism():
    gt1 = generate_data.compute_ground_truth(generate_data.generate(random.Random(42)))
    gt2 = generate_data.compute_ground_truth(generate_data.generate(random.Random(42)))
    assert gt1 == gt2
    assert json.dumps(gt1, sort_keys=True) == json.dumps(gt2, sort_keys=True)


def test_generator_seed_changes_output():
    gt1 = generate_data.compute_ground_truth(generate_data.generate(random.Random(42)))
    gt2 = generate_data.compute_ground_truth(generate_data.generate(random.Random(7)))
    assert gt1 != gt2


def test_all_cases_validate():
    cases, errors = runner.load_cases(str(CASES_PATH))
    assert errors == []
    assert 55 <= len(cases) <= 65
    for c in cases:
        assert isinstance(c, EvalCase)


def test_case_ids_unique():
    cases, _ = runner.load_cases(str(CASES_PATH))
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_ground_truth_keys_exist():
    cases, _ = runner.load_cases(str(CASES_PATH))
    gt = generate_data.compute_ground_truth(generate_data.generate(random.Random(42)))
    for c in cases:
        key = c.expected.ground_truth_key
        if key is not None:
            assert key in gt, f"{c.id} references missing ground_truth_key {key}"


def test_numeric_cases_have_a_target():
    cases, _ = runner.load_cases(str(CASES_PATH))
    for c in cases:
        if c.expected.type == "numeric":
            assert c.expected.ground_truth_key is not None or c.expected.value is not None


def test_dry_mode_passes(tmp_path):
    db_path, gt = _build_db(tmp_path)
    (tmp_path / "gt.json").write_text(json.dumps(gt))
    cases, _ = runner.load_cases(str(CASES_PATH))
    issues = runner.run_dry(cases, str(db_path), gt)
    assert issues == [], issues


def test_dry_mode_flags_missing_key(tmp_path):
    db_path, gt = _build_db(tmp_path)
    cases, _ = runner.load_cases(str(CASES_PATH))
    broken = dict(gt)
    broken.pop("total_calls", None)
    issues = runner.run_dry(cases, str(db_path), broken)
    assert any("total_calls" in i for i in issues)


def test_numeric_grader_currency_and_commas():
    assert graders.extract_numbers("$1,234.5") == [1234.5]
    assert graders.grade_numeric("The total is $1,234.5 dollars", 1234.5)
    assert graders.grade_numeric("about 45%", 45.0)
    assert graders.grade_numeric("1,332 calls", 1332)
    assert not graders.grade_numeric("1,338 calls", 1332)


def test_numeric_grader_tolerance():
    assert graders.grade_numeric("average was 5.46", 5.47, tolerance=0.01)
    assert not graders.grade_numeric("average was 5.40", 5.47, tolerance=0.01)
    assert graders.grade_numeric("we saw 764 active", 764)


def test_extract_numbers_multiple():
    nums = graders.extract_numbers("From 1,000 rows, 250 matched, giving 25.0%")
    assert 1000.0 in nums and 250.0 in nums and 25.0 in nums


def test_sql_property_grader():
    sql = "SELECT COUNT(*) FROM calls c JOIN sites s ON c.site_id = s.id"
    assert graders.grade_sql_property(sql, must_reference=["calls", "sites"])
    assert not graders.grade_sql_property(sql, must_reference=["enrollments"])
    assert graders.grade_sql_property(sql, must_not_reference=["free_text"])
    assert not graders.grade_sql_property(sql, must_not_reference=["calls"])
    # word-boundary: 'call_queues' must not match a requirement for 'calls'
    assert not graders.grade_sql_property("SELECT * FROM call_queues", must_reference=["calls"])


def test_refusal_grader_case_insensitive():
    ans = "I Cannot quantify categories from the restricted free text column."
    assert graders.grade_text_constraints(ans, must_include_any=["cannot", "restricted"])
    assert not graders.grade_text_constraints(ans, must_include_any=["appointments"])
    assert graders.grade_text_constraints(ans, must_not_include=["12 patients"])
    assert not graders.grade_text_constraints("There are 12 patients", must_not_include=["12 patients"])


def test_extract_sql_and_readonly_guard(tmp_path):
    db_path, _ = _build_db(tmp_path)
    answer = "```sql\nSELECT COUNT(*) FROM calls\n```\nThere are 20000 calls."
    sql = runner.extract_sql(answer)
    assert sql and sql.lower().startswith("select")
    val, err = runner.execute_sql_readonly(str(db_path), sql)
    assert err is None and val == 20000.0
    # non-select rejected
    _, err2 = runner.execute_sql_readonly(str(db_path), "DELETE FROM calls")
    assert err2 is not None
    # multiple statements rejected
    _, err3 = runner.execute_sql_readonly(str(db_path), "SELECT 1; SELECT 2")
    assert err3 is not None


def test_grade_case_numeric_uses_ground_truth(tmp_path):
    cases, _ = runner.load_cases(str(CASES_PATH))
    gt = generate_data.compute_ground_truth(generate_data.generate(random.Random(42)))
    case = next(c for c in cases if c.id == "sc-001")
    res = runner.grade_case(case, "There are 20000 calls.", "SELECT COUNT(*) FROM calls", 20000.0, gt)
    assert res.answer_pass and res.sql_pass and res.passed


def test_grade_case_refusal(tmp_path):
    cases, _ = runner.load_cases(str(CASES_PATH))
    gt = {}
    case = next(c for c in cases if c.category == "restricted_free_text_refusal")
    good = "I cannot produce counts from the restricted free text; use structured note_status instead."
    res = runner.grade_case(case, good, None, None, gt)
    assert res.answer_pass


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
