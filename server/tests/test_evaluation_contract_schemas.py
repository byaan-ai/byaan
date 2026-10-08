from __future__ import annotations

import pytest
from pydantic import ValidationError

from server.schemas.evaluation import (
    EvaluationCaseContract,
    EvaluationSuiteVersionManifest,
    EvaluationTargetSnapshot,
)


def _valid_expected_contract() -> dict:
    return {
        "semantic_intent": {
            "metric": "revenue",
            "dimensions": ["region"],
            "grain": "month",
            "timezone": "UTC",
        },
        "ground_truth_sql": {
            "sql": "SELECT region, SUM(revenue) AS revenue FROM fact_sales GROUP BY region",
            "dialect": "duckdb",
            "must_reference": ["fact_sales"],
        },
        "expected_schema": [
            {"name": "region", "data_type": "string"},
            {"name": "revenue", "data_type": "number", "unit": "USD"},
        ],
        "normalized_result": {
            "mode": "multiset",
            "rows": [{"region": "AMER", "revenue": "123.45"}],
            "canonical_hash": "sha256:result",
        },
        "tolerance": {"absolute": 0.01, "relative": 0.001},
        "answer": {"must_include_any": ["AMER"], "must_not_include": ["restricted"]},
        "evidence": {"required": True, "lineage_refs": ["source-1"]},
        "policy": {"required_scopes": ["dashboard.export"], "forbidden_fields": ["free_text"]},
        "dashboard": {"manifest_id": "dash-1", "run_contract_version": "dashboard.run.v1"},
        "human_mcp_parity": {"required": True, "compare_fields": ["values", "units", "warnings"]},
    }


def _valid_case() -> dict:
    return {
        "contract_version": "evaluation.case.v1",
        "case_id": "case-revenue-by-region",
        "title": "Revenue by region",
        "target_kinds": ["semantic_model", "dashboard"],
        "operation": "answer_question",
        "question": "What is revenue by region?",
        "expected": _valid_expected_contract(),
        "tags": ["finance"],
        "provenance": {"source": "human_feedback", "feedback_id": "fb-1"},
    }


def _complete_target_snapshot(target_kind: str = "dashboard") -> dict:
    return {
        "contract_version": "evaluation.target_snapshot.v1",
        "target_kind": target_kind,
        "target_ref": "dashboard:dash-1",
        "app": {
            "git_sha": "abc123",
            "image_digest": "sha256:image",
            "migration_revision": "add_evaluation_authoritative_model",
        },
        "connector": {"version": "connector-v1"},
        "source": {"snapshot_id": "source-1", "snapshot_hash": "sha256:source"},
        "semantic_model": {"version_id": "semver-1", "version_hash": "sha256:semantic"},
        "dashboard": {
            "version_id": "dash-version-1",
            "manifest_hash": "sha256:manifest",
            "renderer_version": "renderer-v1",
        },
        "prompt": {"version": "prompt-v1"},
        "tool_registry_hash": "sha256:tools",
        "skill_registry_hash": "sha256:skills",
        "llm": {"provider": "openai", "model": "gpt-5", "params_hash": "sha256:llm"},
        "principal": {
            "tenant_id": "tenant-1",
            "actor_type": "agent",
            "actor_id": "agent-1",
            "scopes": ["dashboard.export"],
            "rls": {"region": "AMER"},
            "cls": ["revenue"],
        },
        "dataset": {"snapshot_id": "dataset-1", "snapshot_hash": "sha256:dataset"},
        "feature_flags": {"evaluation_governance": True},
        "time_fixture": {"now": "2026-08-16T00:00:00Z", "timezone": "UTC"},
    }


def test_evaluation_case_contract_accepts_authoritative_expected_sections() -> None:
    case = EvaluationCaseContract.model_validate(_valid_case())

    assert case.contract_version == "evaluation.case.v1"
    assert case.expected.ground_truth_sql is not None
    assert case.expected.human_mcp_parity.required is True


def test_evaluation_case_contract_rejects_non_readonly_ground_truth_sql() -> None:
    payload = _valid_case()
    payload["expected"]["ground_truth_sql"]["sql"] = "DELETE FROM fact_sales"

    with pytest.raises(ValidationError, match="read-only"):
        EvaluationCaseContract.model_validate(payload)


def test_evaluation_manifest_json_schema_requires_authoritative_sections() -> None:
    schema = EvaluationSuiteVersionManifest.model_json_schema()

    assert schema["properties"]["contract_version"]["const"] == "evaluation.suite_version.v1"
    assert {"contract_version", "suite_id", "version", "cases", "gate_policy"}.issubset(schema["required"])


def test_target_snapshot_reports_missing_required_pins_without_latest_fallback() -> None:
    snapshot = EvaluationTargetSnapshot.model_validate(
        {
            "contract_version": "evaluation.target_snapshot.v1",
            "target_kind": "semantic_model",
            "target_ref": "semantic_model:sales",
            "app": {"git_sha": "abc123"},
            "principal": {"tenant_id": "tenant-1", "actor_type": "human", "actor_id": "user-1", "scopes": []},
            "feature_flags": {},
            "time_fixture": {"timezone": "UTC"},
        }
    )

    blockers = snapshot.required_pin_blockers()

    assert "app.image_digest" in blockers
    assert "app.migration_revision" in blockers
    assert "source.snapshot_hash" in blockers
    assert "semantic_model.version_hash" in blockers
    assert "time_fixture.now" in blockers


def test_target_snapshot_accepts_complete_dashboard_pins() -> None:
    snapshot = EvaluationTargetSnapshot.model_validate(_complete_target_snapshot("dashboard"))

    assert snapshot.required_pin_blockers() == []
