"""Tests for the synthetic eval harness. No LLM calls, no network.

Run: cd server && PYTHONPATH=..:tests uv run pytest tests/test_evals_harness.py -q
"""

from __future__ import annotations

import json
import random
import subprocess
from pathlib import Path

import pytest
from evals.harness import engines, graders, judge, runner
from evals.harness.engines import ClaudeCliEngine, CodexEngine, EngineResponse, get_engine
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


def test_engine_selection_wiring():
    assert isinstance(get_engine("litellm"), engines.LitellmEngine)
    assert isinstance(get_engine("codex"), CodexEngine)
    assert isinstance(get_engine("claude-cli"), ClaudeCliEngine)
    assert isinstance(get_engine("claude-agentic"), engines.ClaudeAgenticEngine)
    assert isinstance(get_engine("codex-agentic"), engines.CodexAgenticEngine)
    assert engines.ENGINE_CHOICES == ["litellm", "codex", "claude-cli", "claude-agentic", "codex-agentic"]
    with pytest.raises(ValueError):
        get_engine("nope")
    # CLI engines cap concurrency; litellm does not
    assert CodexEngine().max_concurrency == 3
    assert ClaudeCliEngine().max_concurrency == 3
    assert engines.ClaudeAgenticEngine().max_concurrency == 4
    assert engines.CodexAgenticEngine().max_concurrency == 4
    assert engines.LitellmEngine().max_concurrency is None


def test_codex_command_construction_and_outfile_parsing(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        outfile = cmd[cmd.index("-o") + 1]
        Path(outfile).write_text("SELECT 1\nfinal answer 42\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    eng = CodexEngine(binary="/fake/codex")
    resp = eng.complete("SYS PROMPT", "USER Q", "gpt-5.6-sol", reasoning_effort="low", timeout=123.0)

    cmd = captured["cmd"]
    assert cmd[0] == "/fake/codex"
    assert cmd[1] == "exec"
    assert cmd[cmd.index("--model") + 1] == "gpt-5.6-sol"
    assert "-c" in cmd and "model_reasoning_effort=low" in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in cmd
    # combined system+user prompt is the trailing positional arg
    prompt_arg = cmd[-1]
    assert "SYS PROMPT" in prompt_arg and "USER Q" in prompt_arg
    assert captured["kwargs"]["cwd"] == "/tmp"
    assert captured["kwargs"]["timeout"] == 123.0
    assert resp.text == "SELECT 1\nfinal answer 42"
    assert resp.raw_error is None


def test_codex_empty_output_is_error(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    resp = CodexEngine(binary="/fake/codex").complete("s", "u", "m")
    assert resp.text == ""
    assert resp.raw_error and "empty output" in resp.raw_error


def test_claude_command_construction_and_stdin(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="the answer\n", stderr="")

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    eng = ClaudeCliEngine(binary="/fake/claude")
    resp = eng.complete("SYS PROMPT", "USER Q", "claude-opus-4-8", timeout=77.0)

    cmd = captured["cmd"]
    assert cmd[0] == "/fake/claude"
    assert "-p" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYS PROMPT"
    # isolation flags
    assert "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert "--disallowedTools" in cmd and "Bash" in cmd
    # user prompt delivered via stdin, not argv
    assert captured["kwargs"]["input"] == "USER Q"
    assert "USER Q" not in cmd
    assert captured["kwargs"]["cwd"] == "/tmp"
    assert captured["kwargs"]["timeout"] == 77.0
    assert resp.text == "the answer"


def test_engine_timeout_returns_error_not_exception(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    for eng in (CodexEngine(binary="/fake/codex"), ClaudeCliEngine(binary="/fake/claude")):
        resp = eng.complete("s", "u", "m", timeout=5.0)
        assert isinstance(resp, EngineResponse)
        assert resp.text == ""
        assert resp.raw_error and "timeout" in resp.raw_error


class _ScriptedEngine:
    """Engine stub returning a scripted sequence of EngineResponses."""

    max_concurrency = None

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system_prompt, user_prompt, model, reasoning_effort=None, timeout=300.0):
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp


def test_retry_recorded_on_transient_error(tmp_path):
    db_path, gt = _build_db(tmp_path)
    cases, _ = runner.load_cases(str(CASES_PATH))
    case = next(c for c in cases if c.id == "sc-001")
    good = EngineResponse(text="```sql\nSELECT COUNT(*) FROM calls\n```\n20000 calls.", latency_s=0.1)
    engine = _ScriptedEngine([EngineResponse(text="", latency_s=0.1, raw_error="boom"), good])
    res = runner.run_case_prompt_only(case, "m", str(db_path), "sys", gt, engine, engine, judge_model=None)
    assert engine.calls == 2
    assert res.retries == 1
    assert res.error is None
    assert res.passed


def test_retry_exhausted_records_error(tmp_path):
    db_path, gt = _build_db(tmp_path)
    cases, _ = runner.load_cases(str(CASES_PATH))
    case = next(c for c in cases if c.id == "sc-001")
    engine = _ScriptedEngine([EngineResponse(text="", latency_s=0.1, raw_error="boom")])
    res = runner.run_case_prompt_only(case, "m", str(db_path), "sys", gt, engine, engine, judge_model=None)
    assert engine.calls == 2
    assert res.retries == 1
    assert res.error and "boom" in res.error
    assert not res.passed


def test_judge_uses_engine(monkeypatch):
    engine = _ScriptedEngine([EngineResponse(text='{"pass": true, "reason": "ok"}', latency_s=0.1)])
    verdict = judge.judge_answer("rubric", "q", "a", "judge-model", engine)
    assert verdict["pass"] is True and verdict["reason"] == "ok"
    assert engine.calls == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
