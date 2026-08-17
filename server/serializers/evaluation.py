from __future__ import annotations

from datetime import datetime
from typing import Any

from server.models.evaluation import (
    AdvisorChangeSet,
    AdvisorSuggestion,
    EvaluationArtifact,
    EvaluationAssessment,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    PromotionDecision,
)
from server.utils.error_sanitizer import sanitize_error_payload


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def evaluation_suite_payload(
    suite: EvaluationSuite,
    *,
    versions: list[EvaluationSuiteVersion] | None = None,
    include_versions: bool = False,
    include_manifests: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(suite.id),
        "tenant_id": str(suite.tenant_id),
        "slug": suite.slug,
        "name": suite.name,
        "description": suite.description,
        "owner_id": str(suite.owner_id) if suite.owner_id else None,
        "target_kinds": suite.target_kinds_json or [],
        "lifecycle": suite.lifecycle,
        "current_draft_version_id": str(suite.current_draft_version_id) if suite.current_draft_version_id else None,
        "published_version_id": str(suite.published_version_id) if suite.published_version_id else None,
        "created_at": _dt(suite.created_at),
        "updated_at": _dt(suite.updated_at),
    }
    if include_versions:
        payload["versions"] = [
            evaluation_suite_version_payload(version, include_manifest=include_manifests)
            for version in versions or []
        ]
    return payload


def evaluation_suite_version_payload(
    version: EvaluationSuiteVersion,
    *,
    include_manifest: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(version.id),
        "tenant_id": str(version.tenant_id),
        "suite_id": str(version.suite_id),
        "version_num": version.version_num,
        "status": version.status,
        "contract_version": version.contract_version,
        "case_count": version.case_count,
        "content_hash": version.content_hash,
        "gate_policy": sanitize_error_payload(version.gate_policy_json or {}),
        "created_by": str(version.created_by) if version.created_by else None,
        "published_at": _dt(version.published_at),
        "created_at": _dt(version.created_at),
    }
    if include_manifest:
        payload["manifest"] = sanitize_error_payload(version.manifest_json or {})
    return payload


def evaluation_run_payload(run: EvaluationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "tenant_id": str(run.tenant_id),
        "suite_version_id": str(run.suite_version_id),
        "target_snapshot_id": str(run.target_snapshot_id),
        "status": run.status,
        "actor_type": run.actor_type,
        "actor_id": run.actor_id,
        "baseline_run_id": str(run.baseline_run_id) if run.baseline_run_id else None,
        "candidate_label": run.candidate_label,
        "idempotency_key": run.idempotency_key,
        "attempt": run.attempt,
        "lease_holder": run.lease_holder,
        "lease_expires_at": _dt(run.lease_expires_at),
        "heartbeat_at": _dt(run.heartbeat_at),
        "stop_requested": run.stop_requested,
        "preflight_blockers": run.preflight_blockers_json or [],
        "summary": sanitize_error_payload(run.summary_json or {}),
        "started_at": _dt(run.started_at),
        "completed_at": _dt(run.completed_at),
        "created_at": _dt(run.created_at),
    }


def evaluation_case_payload(
    case: EvaluationCase,
    *,
    include_expected_contract: bool = True,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": str(case.id),
        "tenant_id": str(case.tenant_id),
        "suite_version_id": str(case.suite_version_id),
        "case_key": case.case_key,
        "title": case.title,
        "target_kinds": case.target_kinds_json or [],
        "operation": case.operation,
        "question": case.question,
        "provenance": sanitize_error_payload(case.provenance_json or {}),
        "tags": case.tags_json or [],
        "content_hash": case.content_hash,
        "immutable": case.immutable,
        "has_ground_truth_sql": bool((case.expected_contract_json or {}).get("ground_truth_sql")),
        "created_at": _dt(case.created_at),
    }
    if include_expected_contract:
        payload["expected_contract"] = sanitize_error_payload(case.expected_contract_json or {})
    return payload


def evaluation_case_run_payload(
    case_run: EvaluationCaseRun,
    *,
    assessments: list[EvaluationAssessment] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(case_run.id),
        "tenant_id": str(case_run.tenant_id),
        "run_id": str(case_run.run_id),
        "case_id": str(case_run.case_id),
        "status": case_run.status,
        "attempt": case_run.attempt,
        "input_digest": case_run.input_digest,
        "output_digest": case_run.output_digest,
        "result": sanitize_error_payload(case_run.result_json or {}),
        "error": sanitize_error_payload(case_run.error_json or {}),
        "immutable": case_run.immutable,
        "started_at": _dt(case_run.started_at),
        "completed_at": _dt(case_run.completed_at),
        "created_at": _dt(case_run.created_at),
        "assessments": [evaluation_assessment_payload(assessment) for assessment in assessments or []],
    }


def evaluation_assessment_payload(assessment: EvaluationAssessment) -> dict[str, Any]:
    return {
        "id": str(assessment.id),
        "tenant_id": str(assessment.tenant_id),
        "case_run_id": str(assessment.case_run_id),
        "category": assessment.category,
        "status": assessment.status,
        "score": assessment.score,
        "hard_fail": assessment.hard_fail,
        "details": sanitize_error_payload(assessment.details_json or {}),
        "immutable": assessment.immutable,
        "created_at": _dt(assessment.created_at),
    }


def evaluation_artifact_payload(artifact: EvaluationArtifact) -> dict[str, Any]:
    return {
        "id": str(artifact.id),
        "tenant_id": str(artifact.tenant_id),
        "run_id": str(artifact.run_id) if artifact.run_id else None,
        "case_run_id": str(artifact.case_run_id) if artifact.case_run_id else None,
        "artifact_type": artifact.artifact_type,
        "uri": artifact.uri,
        "content_hash": artifact.content_hash,
        "metadata": sanitize_error_payload(artifact.metadata_json or {}),
        "immutable": artifact.immutable,
        "created_at": _dt(artifact.created_at),
    }


def promotion_payload(promotion: PromotionDecision) -> dict[str, Any]:
    return {
        "id": str(promotion.id),
        "tenant_id": str(promotion.tenant_id),
        "change_set_id": str(promotion.change_set_id) if promotion.change_set_id else None,
        "verification_run_id": str(promotion.verification_run_id) if promotion.verification_run_id else None,
        "regression_run_id": str(promotion.regression_run_id) if promotion.regression_run_id else None,
        "decision": promotion.decision,
        "decided_by": str(promotion.decided_by) if promotion.decided_by else None,
        "rationale": promotion.rationale,
        "audit": sanitize_error_payload(promotion.audit_json or {}),
        "created_at": _dt(promotion.created_at),
    }


def advisor_change_set_payload(change_set: AdvisorChangeSet) -> dict[str, Any]:
    return {
        "id": str(change_set.id),
        "tenant_id": str(change_set.tenant_id),
        "suite_version_id": str(change_set.suite_version_id) if change_set.suite_version_id else None,
        "target_ref": change_set.target_ref,
        "base_version_ref": change_set.base_version_ref,
        "base_etag": change_set.base_etag,
        "status": change_set.status,
        "evidence": sanitize_error_payload(change_set.evidence_json or {}),
        "verification_run_id": str(change_set.verification_run_id) if change_set.verification_run_id else None,
        "regression_run_id": str(change_set.regression_run_id) if change_set.regression_run_id else None,
        "created_by": change_set.created_by,
        "created_at": _dt(change_set.created_at),
    }


def advisor_suggestion_payload(suggestion: AdvisorSuggestion) -> dict[str, Any]:
    return {
        "id": str(suggestion.id),
        "tenant_id": str(suggestion.tenant_id),
        "change_set_id": str(suggestion.change_set_id),
        "suggestion_type": suggestion.suggestion_type,
        "patch": sanitize_error_payload(suggestion.patch_json or {}),
        "affected_case_ids": suggestion.affected_case_ids_json or [],
        "status": suggestion.status,
        "created_at": _dt(suggestion.created_at),
    }
