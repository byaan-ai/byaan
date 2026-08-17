from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.conversation_evaluation import ConversationEvaluation
from server.models.custom_skill import CustomSkill
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
from server.models.notebooks import Notebook
from server.models.skill_suggestion import SkillSuggestion
from server.repositories.evaluation import EvaluationRepository
from server.schemas.evaluation import EvaluationExpectedContract, EvaluationTargetSnapshot
from server.utils.error_sanitizer import sanitize_error_payload

SUPPORTED_TARGET_KINDS = {"connector", "semantic_model", "agent_answer", "dashboard", "policy", "end_to_end"}
SUPPORTED_OPERATIONS = {"answer_question", "execute_sql", "query_dashboard", "apply_policy", "end_to_end_task"}


class EvaluationService:
    def __init__(self, session: AsyncSession):
        self._session = session
        self._repo = EvaluationRepository(session)

    async def list_suites(
        self,
        *,
        tenant_id: str | UUID,
        query: str = "",
        target_kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[EvaluationSuite]:
        return await self._repo.list_suites(
            tenant_id=tenant_id,
            query=query,
            target_kind=target_kind,
            status=status,
            limit=limit,
        )

    async def describe_suite(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID,
    ) -> tuple[EvaluationSuite, list[EvaluationSuiteVersion]]:
        suite = await self._repo.get_suite(tenant_id=tenant_id, suite_id=suite_id)
        if suite is None:
            raise ValueError("evaluation suite not found")
        versions = await self._repo.list_suite_versions(tenant_id=tenant_id, suite_id=suite.id)
        return suite, versions

    async def create_suite_with_draft(
        self,
        *,
        tenant_id: str | UUID,
        slug: str,
        name: str,
        description: str,
        target_kinds: list[str],
        gate_policy: dict[str, Any],
        actor_id: str | UUID,
    ) -> tuple[EvaluationSuite, EvaluationSuiteVersion]:
        if await self._repo.get_suite_by_slug(tenant_id=tenant_id, slug=slug):
            raise ValueError("evaluation suite slug already exists")
        normalized_target_kinds = self._validate_target_kinds(target_kinds)
        gate_policy_json = self._default_gate_policy(gate_policy)
        manifest = {
            "contract_version": "evaluation.suite_version.v1",
            "suite_id": slug,
            "version": 1,
            "cases": [],
            "gate_policy": gate_policy_json,
            "owner": str(actor_id),
            "description": description,
        }
        suite = await self._repo.create_suite(
            tenant_id=tenant_id,
            slug=slug,
            name=name,
            description=description,
            owner_id=self._optional_uuid(actor_id),
            target_kinds_json=normalized_target_kinds,
            lifecycle="draft",
        )
        version = await self._repo.create_suite_version(
            tenant_id=tenant_id,
            suite_id=suite.id,
            version_num=1,
            status="draft",
            contract_version="evaluation.suite_version.v1",
            manifest_json=manifest,
            gate_policy_json=gate_policy_json,
            case_count=0,
            content_hash=self._digest(manifest),
            created_by=self._optional_uuid(actor_id),
        )
        suite.current_draft_version_id = version.id
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite.id,
            suite_version_id=version.id,
            run_id=None,
            actor_type="human",
            actor_id=str(actor_id),
            action="evaluation.suite.create",
            outcome="draft_created",
            details_json={"slug": slug, "target_kinds": normalized_target_kinds},
        )
        await self._session.commit()
        await self._session.refresh(suite)
        await self._session.refresh(version)
        return suite, version

    async def create_draft_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID,
        actor_id: str | UUID,
        copy_from_version_id: str | UUID | None = None,
    ) -> EvaluationSuiteVersion:
        suite = await self._repo.get_suite(tenant_id=tenant_id, suite_id=suite_id)
        if suite is None:
            raise ValueError("evaluation suite not found")
        versions = await self._repo.list_suite_versions(tenant_id=tenant_id, suite_id=suite.id)
        next_version_num = max((version.version_num for version in versions), default=0) + 1
        source_version = None
        if copy_from_version_id is not None:
            source_version = await self._require_suite_version(
                tenant_id=tenant_id,
                suite_version_id=copy_from_version_id,
            )
            if source_version.suite_id != suite.id:
                raise ValueError("evaluation source version does not belong to suite")
        gate_policy_json = self._default_gate_policy(source_version.gate_policy_json if source_version else {})
        manifest = {
            "contract_version": "evaluation.suite_version.v1",
            "suite_id": suite.slug,
            "version": next_version_num,
            "cases": [],
            "gate_policy": gate_policy_json,
            "owner": str(actor_id),
            "description": suite.description,
            "copied_from_version_id": str(source_version.id) if source_version else None,
        }
        version = await self._repo.create_suite_version(
            tenant_id=tenant_id,
            suite_id=suite.id,
            version_num=next_version_num,
            status="draft",
            contract_version="evaluation.suite_version.v1",
            manifest_json=manifest,
            gate_policy_json=gate_policy_json,
            case_count=0,
            content_hash=self._digest(manifest),
            created_by=self._optional_uuid(actor_id),
        )
        suite.current_draft_version_id = version.id
        suite.lifecycle = "draft"
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite.id,
            suite_version_id=version.id,
            run_id=None,
            actor_type="human",
            actor_id=str(actor_id),
            action="evaluation.suite_version.create_draft",
            outcome="draft_created",
            details_json={"version_num": next_version_num, "copied_from_version_id": manifest["copied_from_version_id"]},
        )
        await self._session.commit()
        await self._session.refresh(version)
        return version

    async def list_cases(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
    ) -> list[EvaluationCase]:
        await self._require_suite_version(tenant_id=tenant_id, suite_version_id=suite_version_id)
        return await self._repo.list_cases_for_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )

    async def list_runs(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        limit: int = 50,
    ) -> list[EvaluationRun]:
        await self._require_suite_version(tenant_id=tenant_id, suite_version_id=suite_version_id)
        return await self._repo.list_runs_for_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
            limit=limit,
        )

    async def get_run_report(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
    ) -> tuple[EvaluationRun, list[tuple[EvaluationCaseRun, list[EvaluationAssessment]]]]:
        run = await self._repo.get_run(tenant_id=tenant_id, run_id=run_id)
        if run is None:
            raise ValueError("evaluation run not found")
        case_runs = await self._repo.list_case_runs_for_run(tenant_id=tenant_id, run_id=run.id)
        assessments = await self._repo.list_assessments_for_case_runs(
            tenant_id=tenant_id,
            case_run_ids=[case_run.id for case_run in case_runs],
        )
        assessments_by_case_run: dict[str, list[EvaluationAssessment]] = {}
        for assessment in assessments:
            assessments_by_case_run.setdefault(str(assessment.case_run_id), []).append(assessment)
        return run, [
            (case_run, assessments_by_case_run.get(str(case_run.id), []))
            for case_run in case_runs
        ]

    async def compare_runs(
        self,
        *,
        tenant_id: str | UUID,
        baseline_run_id: str | UUID,
        candidate_run_id: str | UUID,
    ) -> dict[str, Any]:
        baseline, baseline_case_runs = await self.get_run_report(tenant_id=tenant_id, run_id=baseline_run_id)
        candidate, candidate_case_runs = await self.get_run_report(tenant_id=tenant_id, run_id=candidate_run_id)
        baseline_by_case = {str(case_run.case_id): case_run for case_run, _ in baseline_case_runs}
        candidate_by_case = {str(case_run.case_id): case_run for case_run, _ in candidate_case_runs}
        all_case_ids = sorted(set(baseline_by_case) | set(candidate_by_case))
        regressions = []
        improvements = []
        unchanged = []
        for case_id in all_case_ids:
            baseline_status = baseline_by_case.get(case_id).status if case_id in baseline_by_case else "missing"
            candidate_status = candidate_by_case.get(case_id).status if case_id in candidate_by_case else "missing"
            item = {"case_id": case_id, "baseline_status": baseline_status, "candidate_status": candidate_status}
            if baseline_status == "passed" and candidate_status != "passed":
                regressions.append(item)
            elif baseline_status != "passed" and candidate_status == "passed":
                improvements.append(item)
            else:
                unchanged.append(item)
        return {
            "baseline_run_id": str(baseline.id),
            "candidate_run_id": str(candidate.id),
            "baseline_gate": self._run_gate_decision(baseline),
            "candidate_gate": self._run_gate_decision(candidate),
            "regressions": regressions,
            "improvements": improvements,
            "unchanged": unchanged,
            "summary": {
                "regression_count": len(regressions),
                "improvement_count": len(improvements),
                "unchanged_count": len(unchanged),
            },
        }

    async def list_advisor_change_sets(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        limit: int = 50,
    ) -> list[AdvisorChangeSet]:
        await self._require_suite_version(tenant_id=tenant_id, suite_version_id=suite_version_id)
        return await self._repo.list_change_sets_for_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
            limit=limit,
        )

    async def create_preflight_run(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        target_snapshot_payload: dict[str, Any],
        actor_id: str,
        idempotency_key: str | None = None,
        actor_type: str = "agent",
    ) -> EvaluationRun:
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        if idempotency_key:
            existing = await self._repo.get_run_by_idempotency_key(
                tenant_id=tenant_id,
                suite_version_id=suite_version.id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
        target_snapshot = EvaluationTargetSnapshot.model_validate(target_snapshot_payload)
        blockers = target_snapshot.required_pin_blockers()
        snapshot_json = target_snapshot.model_dump(mode="json")
        snapshot = await self._repo.create_target_snapshot(
            tenant_id=tenant_id,
            target_kind=target_snapshot.target_kind,
            target_ref=target_snapshot.target_ref,
            contract_version=target_snapshot.contract_version,
            snapshot_json=snapshot_json,
            pin_digest=self._digest(snapshot_json),
            blockers_json=blockers,
        )
        status = "blocked" if blockers else "queued"
        run = await self._repo.create_run(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            target_snapshot_id=snapshot.id,
            status=status,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            preflight_blockers_json=blockers,
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=run.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="evaluation.run.preflight",
            outcome=status,
            details_json={"blockers": blockers},
        )
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def heartbeat_run(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> EvaluationRun:
        run = await self._require_worker_run(
            tenant_id=tenant_id,
            run_id=run_id,
            worker_id=worker_id,
        )
        now = datetime.utcnow()
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        outcome = "running"
        if run.stop_requested:
            run.status = "canceled"
            run.completed_at = now
            run.summary_json = {**(run.summary_json or {}), "gate_decision": "canceled", "stop_requested": True}
            outcome = "canceled"
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=run.id,
            actor_type="worker",
            actor_id=worker_id,
            action="evaluation.run.heartbeat",
            outcome=outcome,
            details_json={"lease_seconds": lease_seconds},
        )
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def request_run_stop(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        actor_id: str,
        actor_type: str = "human",
    ) -> EvaluationRun:
        run = await self._repo.get_run(tenant_id=tenant_id, run_id=run_id)
        if run is None:
            raise ValueError("evaluation run not found")
        run.stop_requested = True
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=run.id,
            actor_type=actor_type,
            actor_id=actor_id,
            action="evaluation.run.stop_request",
            outcome="requested",
            details_json={},
        )
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def record_run_artifact(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        artifact_type: str,
        uri: str,
        content: Any,
    ) -> EvaluationArtifact:
        run = await self._repo.get_run(tenant_id=tenant_id, run_id=run_id)
        if run is None:
            raise ValueError("evaluation run not found")
        content_hash = self._digest(content)
        artifact = await self._repo.create_artifact(
            tenant_id=tenant_id,
            run_id=run.id,
            case_run_id=None,
            artifact_type=artifact_type,
            uri=uri,
            content_hash=content_hash,
            metadata_json={"content_hash": content_hash},
        )
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=run.id,
            actor_type="service",
            actor_id="evaluation-service",
            action="evaluation.artifact.record",
            outcome="created",
            details_json={"artifact_type": artifact_type, "uri": uri, "content_hash": content_hash},
        )
        await self._session.commit()
        await self._session.refresh(artifact)
        return artifact

    async def decide_promotion(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
        actor_id: str,
    ) -> PromotionDecision:
        change_set = await self._repo.get_change_set(tenant_id=tenant_id, change_set_id=change_set_id)
        if change_set is None:
            raise ValueError("advisor change set not found")
        verification_run = await self._repo.get_run(
            tenant_id=tenant_id,
            run_id=change_set.verification_run_id,
        )
        regression_run = await self._repo.get_run(
            tenant_id=tenant_id,
            run_id=change_set.regression_run_id,
        )
        verification_gate = self._run_gate_decision(verification_run)
        regression_gate = self._run_gate_decision(regression_run)
        accepted = verification_gate == "passed" and regression_gate == "passed"
        decision = "accepted" if accepted else "rejected"
        change_set.status = "promoted" if accepted else "rejected"
        audit_json = {
            "verification_gate": verification_gate,
            "regression_gate": regression_gate,
            "target_ref": change_set.target_ref,
            "base_version_ref": change_set.base_version_ref,
            "actor_id": actor_id,
        }
        promotion = await self._repo.create_promotion_decision(
            tenant_id=tenant_id,
            change_set_id=change_set.id,
            verification_run_id=change_set.verification_run_id,
            regression_run_id=change_set.regression_run_id,
            decision=decision,
            decided_by=self._optional_uuid(actor_id),
            rationale=(
                "Verification and regression gates passed."
                if accepted
                else "Promotion blocked until verification and regression gates both pass."
            ),
            audit_json=audit_json,
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=None,
            suite_version_id=change_set.suite_version_id,
            run_id=None,
            actor_type="human",
            actor_id=actor_id,
            action="evaluation.promotion.decide",
            outcome=decision,
            details_json=audit_json,
        )
        await self._session.commit()
        await self._session.refresh(promotion)
        return promotion

    async def get_advisor_review(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
    ) -> dict[str, Any]:
        change_set = await self._repo.get_change_set(tenant_id=tenant_id, change_set_id=change_set_id)
        if change_set is None:
            raise ValueError("advisor change set not found")
        suggestions = await self._repo.list_advisor_suggestions_for_change_set(
            tenant_id=tenant_id,
            change_set_id=change_set.id,
        )
        verification_run = (
            await self._repo.get_run(tenant_id=tenant_id, run_id=change_set.verification_run_id)
            if change_set.verification_run_id
            else None
        )
        regression_run = (
            await self._repo.get_run(tenant_id=tenant_id, run_id=change_set.regression_run_id)
            if change_set.regression_run_id
            else None
        )
        promotions = await self._repo.list_promotion_decisions_for_change_set(
            tenant_id=tenant_id,
            change_set_id=change_set.id,
        )
        return {
            "change_set": change_set,
            "advisor_suggestions": suggestions,
            "verification_run": verification_run,
            "regression_run": regression_run,
            "promotion_decisions": promotions,
            "gate_summary": {
                "verification_gate": self._run_gate_decision(verification_run),
                "regression_gate": self._run_gate_decision(regression_run),
                "ready_to_apply": (
                    self._run_gate_decision(verification_run) == "passed"
                    and self._run_gate_decision(regression_run) == "passed"
                ),
            },
        }

    async def promote_conversation_evaluation_to_case_draft(
        self,
        *,
        tenant_id: str | UUID,
        evaluation_id: str | UUID,
        suite_version_id: str | UUID,
        question: str | None,
        tags: list[str],
        actor_id: str,
    ) -> tuple[EvaluationCase, bool]:
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        if suite_version.status == "published":
            raise ValueError("published evaluation suite version manifest is immutable")

        evaluation = await self._get_conversation_evaluation(tenant_id=tenant_id, evaluation_id=evaluation_id)
        if evaluation.verdict not in {"mistake", "ambiguous"}:
            raise ValueError("only mistake or ambiguous conversation evaluations can become case drafts")
        case_key = f"legacy-conversation-evaluation-{evaluation.id}"
        existing = await self._repo.get_case_by_key(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            case_key=case_key,
        )
        if existing is not None:
            return existing, False

        notebook = await self._session.get(Notebook, evaluation.notebook_id)
        findings = sanitize_error_payload(evaluation.findings or {})
        feedback_question = (question or findings.get("question") or findings.get("description") or "").strip()
        if not feedback_question:
            feedback_question = f"Regression from conversation evaluation {evaluation.id}"
        correction = (findings.get("correction") or "").strip()
        summary = (findings.get("summary") or findings.get("description") or "").strip()
        expected_contract = {
            "semantic_intent": {
                "description": summary,
            },
            "answer": {
                "must_include_any": [correction] if correction else [],
                "must_include_all": [],
                "must_not_include": [],
                "refusal_allowed": False,
                "clarification_allowed": evaluation.verdict == "ambiguous",
            },
            "evidence": {
                "required": True,
                "lineage_refs": [str(evaluation.notebook_id)],
            },
            "policy": {
                "required_scopes": [],
                "forbidden_fields": [],
                "expected_decision": None,
                "security_hard_fail": True,
            },
            "feedback": {
                "taxonomy": findings.get("taxonomy"),
                "missed_instruction": findings.get("missed_instruction"),
                "description": findings.get("description") or summary,
                "findings": findings,
            },
        }
        provenance = {
            "source": "legacy_conversation_evaluation",
            "feedback_id": str(evaluation.id),
            "trace_id": findings.get("trace_id"),
            "principal": findings.get("principal") or {},
            "created_at": evaluation.evaluated_at.isoformat() if evaluation.evaluated_at else None,
        }
        case_tags = sorted({*tags, "legacy_conversation_evaluation", f"verdict:{evaluation.verdict}"})
        case = await self._repo.create_case(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            case_key=case_key,
            title=(summary or f"Conversation feedback {evaluation.verdict}")[:255],
            target_kinds_json=["agent_answer"],
            operation="answer_question",
            question=feedback_question,
            expected_contract_json=sanitize_error_payload(expected_contract),
            provenance_json=sanitize_error_payload(provenance),
            tags_json=case_tags,
            content_hash=self._digest(
                {
                    "case_key": case_key,
                    "question": feedback_question,
                    "expected": expected_contract,
                    "provenance": provenance,
                    "tags": case_tags,
                }
            ),
            immutable=False,
        )
        suite_version.case_count += 1
        suite_version.manifest_json = {
            **(suite_version.manifest_json or {}),
            "last_feedback_case_draft_id": str(case.id),
            "last_feedback_notebook_id": str(evaluation.notebook_id),
            "last_feedback_notebook_name": notebook.notebook_name if notebook else None,
        }
        suite_version.content_hash = self._digest(suite_version.manifest_json)
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=None,
            actor_type="human",
            actor_id=actor_id,
            action="evaluation.feedback.promote_to_case",
            outcome="draft_created",
            details_json={
                "legacy_conversation_evaluation_id": str(evaluation.id),
                "case_id": str(case.id),
                "verdict": evaluation.verdict,
            },
        )
        await self._session.commit()
        await self._session.refresh(case)
        return case, True

    async def create_case_draft(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        case_key: str,
        title: str,
        target_kinds: list[str],
        operation: str,
        question: str,
        expected_contract: dict[str, Any],
        provenance: dict[str, Any],
        tags: list[str],
        actor_id: str,
        actor_type: str = "agent",
    ) -> tuple[EvaluationCase, bool]:
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        if suite_version.status == "published":
            raise ValueError("published evaluation suite version manifest is immutable")
        normalized_target_kinds = self._validate_target_kinds(target_kinds)
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError(f"unsupported evaluation operation: {operation}")
        existing = await self._repo.get_case_by_key(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            case_key=case_key,
        )
        if existing is not None:
            return existing, False
        validated_expected = EvaluationExpectedContract.model_validate(expected_contract).model_dump(mode="json")
        sanitized_provenance = sanitize_error_payload(provenance or {})
        sanitized_tags = sorted({str(tag) for tag in tags})
        case = await self._repo.create_case(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            case_key=case_key,
            title=title,
            target_kinds_json=normalized_target_kinds,
            operation=operation,
            question=question,
            expected_contract_json=sanitize_error_payload(validated_expected),
            provenance_json=sanitized_provenance,
            tags_json=sanitized_tags,
            content_hash=self._digest(
                {
                    "case_key": case_key,
                    "question": question,
                    "expected": validated_expected,
                    "provenance": sanitized_provenance,
                    "tags": sanitized_tags,
                }
            ),
            immutable=False,
        )
        suite_version.case_count += 1
        suite_version.manifest_json = {
            **(suite_version.manifest_json or {}),
            "last_manual_case_draft_id": str(case.id),
        }
        suite_version.content_hash = self._digest(suite_version.manifest_json)
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=None,
            actor_type=actor_type,
            actor_id=actor_id,
            action="evaluation.case.create_draft",
            outcome="draft_created",
            details_json={"case_id": str(case.id), "case_key": case.case_key},
        )
        await self._session.commit()
        await self._session.refresh(case)
        return case, True

    async def import_case_drafts(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        cases: list[dict[str, Any]],
        actor_id: str,
        actor_type: str = "human",
    ) -> dict[str, Any]:
        if not cases:
            raise ValueError("evaluation case import requires at least one case")
        errors = []
        for index, payload in enumerate(cases):
            try:
                self._validate_case_import_payload(payload)
            except (KeyError, TypeError, ValueError) as exc:
                errors.append({"index": index, "case_key": payload.get("case_key"), "error": str(exc)})
        if errors:
            raise ValueError(f"evaluation case import validation failed: {errors}")
        imported = []
        existing = []
        for payload in cases:
            case, created = await self.create_case_draft(
                tenant_id=tenant_id,
                suite_version_id=suite_version_id,
                case_key=str(payload["case_key"]),
                title=str(payload.get("title") or payload["case_key"]),
                target_kinds=[str(kind) for kind in payload.get("target_kinds") or []],
                operation=str(payload.get("operation") or "answer_question"),
                question=str(payload["question"]),
                expected_contract=dict(payload.get("expected_contract") or {}),
                provenance=dict(payload.get("provenance") or {"source": "import"}),
                tags=[str(tag) for tag in payload.get("tags") or []],
                actor_id=actor_id,
                actor_type=actor_type,
            )
            (imported if created else existing).append(case)
        return {"created": imported, "existing": existing, "total": len(cases)}

    async def create_advisor_change_set_from_skill_suggestion(
        self,
        *,
        tenant_id: str | UUID,
        suggestion_id: str | UUID,
        suite_version_id: str | UUID | None,
        affected_case_ids: list[str | UUID],
        actor_id: str,
    ) -> tuple[AdvisorChangeSet, list[AdvisorSuggestion], bool]:
        suite_version = None
        if suite_version_id is not None:
            suite_version = await self._require_suite_version(
                tenant_id=tenant_id,
                suite_version_id=suite_version_id,
            )
        suggestion = await self._get_skill_suggestion(tenant_id=tenant_id, suggestion_id=suggestion_id)
        target_ref, base_version_ref, base_etag, patch = await self._advisor_target_and_patch(
            tenant_id=tenant_id,
            suggestion=suggestion,
        )
        existing = await self._repo.get_change_set_for_legacy_skill_suggestion(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id if suite_version else None,
            suggestion_id=suggestion.id,
            target_ref=target_ref,
        )
        if existing is not None:
            advisor_suggestions = await self._repo.list_advisor_suggestions_for_change_set(
                tenant_id=tenant_id,
                change_set_id=existing.id,
            )
            return existing, advisor_suggestions, False

        affected_ids = await self._validate_affected_cases(
            tenant_id=tenant_id,
            suite_version=suite_version,
            affected_case_ids=affected_case_ids,
        )
        evidence = sanitize_error_payload(
            {
                "legacy_skill_suggestion_id": str(suggestion.id),
                "legacy_status": suggestion.status,
                "title": suggestion.title,
                "rationale": suggestion.rationale,
                "confidence": suggestion.confidence,
                "evidence": suggestion.evidence or {},
                "source": suggestion.source or {},
                "created_at": suggestion.created_at.isoformat() if suggestion.created_at else None,
            }
        )
        change_set = await self._repo.create_advisor_change_set(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id if suite_version else None,
            target_ref=target_ref,
            base_version_ref=base_version_ref,
            base_etag=base_etag,
            status="draft",
            evidence_json=evidence,
            created_by=actor_id,
        )
        advisor_suggestion = await self._repo.create_advisor_suggestion(
            tenant_id=tenant_id,
            change_set_id=change_set.id,
            suggestion_type=self._advisor_suggestion_type(suggestion),
            patch_json=sanitize_error_payload(patch),
            affected_case_ids_json=[str(case_id) for case_id in affected_ids],
            status="draft",
        )
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id if suite_version else None,
            suite_version_id=suite_version.id if suite_version else None,
            run_id=None,
            actor_type="human",
            actor_id=actor_id,
            action="evaluation.advisor.change_set.create",
            outcome="draft_created",
            details_json={
                "legacy_skill_suggestion_id": str(suggestion.id),
                "change_set_id": str(change_set.id),
                "target_ref": target_ref,
                "affected_case_ids": [str(case_id) for case_id in affected_ids],
            },
        )
        await self._session.commit()
        await self._session.refresh(change_set)
        await self._session.refresh(advisor_suggestion)
        return change_set, [advisor_suggestion], True

    async def create_advisor_gate_run(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
        gate_kind: str,
        target_snapshot_payload: dict[str, Any],
        actor_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[AdvisorChangeSet, EvaluationRun]:
        if gate_kind not in {"verification", "regression"}:
            raise ValueError("advisor gate kind must be verification or regression")
        change_set = await self._repo.get_change_set(tenant_id=tenant_id, change_set_id=change_set_id)
        if change_set is None:
            raise ValueError("advisor change set not found")
        if change_set.suite_version_id is None:
            raise ValueError("advisor change set has no suite version for evaluation gates")
        run = await self.create_preflight_run(
            tenant_id=tenant_id,
            suite_version_id=change_set.suite_version_id,
            target_snapshot_payload=target_snapshot_payload,
            actor_id=actor_id,
            idempotency_key=idempotency_key or f"advisor-{gate_kind}-{change_set.id}",
            actor_type="agent",
        )
        if gate_kind == "verification":
            change_set.verification_run_id = run.id
            change_set.status = "verification_queued"
        else:
            change_set.regression_run_id = run.id
            change_set.status = "regression_queued"
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=None,
            suite_version_id=change_set.suite_version_id,
            run_id=run.id,
            actor_type="agent",
            actor_id=actor_id,
            action=f"evaluation.advisor.{gate_kind}.run",
            outcome=run.status,
            details_json={"change_set_id": str(change_set.id), "target_ref": change_set.target_ref},
        )
        await self._session.commit()
        await self._session.refresh(change_set)
        await self._session.refresh(run)
        return change_set, run

    async def claim_next_run(
        self,
        *,
        tenant_id: str | UUID,
        worker_id: str,
        lease_seconds: int,
    ) -> EvaluationRun | None:
        now = datetime.utcnow()
        run = await self._repo.get_next_claimable_run(tenant_id=tenant_id, now=now)
        if run is None:
            return None
        previous_holder = run.lease_holder
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        run.status = "running"
        run.lease_holder = worker_id
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.heartbeat_at = now
        run.started_at = run.started_at or now
        run.attempt += 1 if previous_holder and previous_holder != worker_id else 0
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=run.suite_version_id,
            run_id=run.id,
            actor_type="worker",
            actor_id=worker_id,
            action="evaluation.run.claim",
            outcome="running",
            details_json={"lease_seconds": lease_seconds},
        )
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def complete_run_with_case_results(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        worker_id: str,
        case_results: list[dict[str, Any]],
    ) -> EvaluationRun:
        run = await self._repo.get_run(tenant_id=tenant_id, run_id=run_id)
        if run is None:
            raise ValueError("evaluation run not found")
        if run.status != "running" or run.lease_holder != worker_id:
            raise ValueError("evaluation run is not leased by this worker")

        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        cases = await self._repo.list_cases_for_suite_version(
            tenant_id=tenant_id,
            suite_version_id=run.suite_version_id,
        )
        cases_by_key = {case.case_key: case for case in cases}
        result_keys = {str(result.get("case_key")) for result in case_results}
        if result_keys != set(cases_by_key):
            missing = sorted(set(cases_by_key) - result_keys)
            unknown = sorted(result_keys - set(cases_by_key))
            raise ValueError(f"case results do not match suite version cases: missing={missing} unknown={unknown}")

        now = datetime.utcnow()
        hard_failures = 0
        passed_cases = 0
        failed_cases = 0
        for result in case_results:
            case = cases_by_key[str(result["case_key"])]
            status = str(result.get("status") or "failed")
            if status == "passed":
                passed_cases += 1
            else:
                failed_cases += 1
            result_payload = sanitize_error_payload(dict(result.get("result") or {}))
            error_payload = sanitize_error_payload(dict(result.get("error") or {}))
            case_run = await self._repo.create_case_run(
                tenant_id=tenant_id,
                run_id=run.id,
                case_id=case.id,
                status=status,
                attempt=run.attempt,
                input_digest=self._digest(case.expected_contract_json),
                output_digest=self._digest(result_payload),
                result_json=result_payload,
                error_json=error_payload,
                immutable=True,
                started_at=now,
                completed_at=now,
            )
            for assessment_payload in result.get("assessments") or []:
                hard_fail = bool(assessment_payload.get("hard_fail"))
                if hard_fail:
                    hard_failures += 1
                await self._repo.create_assessment(
                    tenant_id=tenant_id,
                    case_run_id=case_run.id,
                    category=str(assessment_payload.get("category") or "overall"),
                    status=str(assessment_payload.get("status") or status),
                    score=assessment_payload.get("score"),
                    hard_fail=hard_fail,
                    details_json=sanitize_error_payload(dict(assessment_payload.get("details") or {})),
                )

        total_cases = len(cases)
        pass_rate = passed_cases / total_cases if total_cases else 0.0
        gate_policy = suite_version.gate_policy_json or {}
        security_hard_fail = bool(gate_policy.get("security_hard_fail", True))
        min_overall_pass_rate = float(gate_policy.get("min_overall_pass_rate", 1.0))
        gate_failed = (security_hard_fail and hard_failures > 0) or pass_rate < min_overall_pass_rate
        run.status = "failed" if gate_failed else "passed"
        run.completed_at = now
        run.summary_json = {
            "total_cases": total_cases,
            "passed_cases": passed_cases,
            "failed_cases": failed_cases,
            "pass_rate": pass_rate,
            "hard_failures": hard_failures,
            "gate_decision": "failed" if gate_failed else "passed",
            "security_hard_fail": security_hard_fail,
            "min_overall_pass_rate": min_overall_pass_rate,
        }
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=run.id,
            actor_type="worker",
            actor_id=worker_id,
            action="evaluation.run.complete",
            outcome=run.status,
            details_json=run.summary_json,
        )
        await self._session.commit()
        await self._session.refresh(run)
        return run

    async def publish_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        actor_id: str,
        actor_type: str = "human",
    ) -> EvaluationSuiteVersion:
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        suite = await self._repo.get_suite(tenant_id=tenant_id, suite_id=suite_version.suite_id)
        if suite is None:
            raise ValueError("evaluation suite not found")
        if suite_version.case_count <= 0:
            raise ValueError("evaluation suite version must contain at least one case before publish")
        cases = await self._repo.list_cases_for_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
        )
        for case in cases:
            case.immutable = True
        suite_version.status = "published"
        suite_version.published_at = datetime.utcnow()
        suite.lifecycle = "published"
        suite.published_version_id = suite_version.id
        if suite.current_draft_version_id == suite_version.id:
            suite.current_draft_version_id = None
        await self._repo.create_audit_event(
            tenant_id=tenant_id,
            suite_id=suite_version.suite_id,
            suite_version_id=suite_version.id,
            run_id=None,
            actor_type=actor_type,
            actor_id=actor_id,
            action="evaluation.suite_version.publish",
            outcome="published",
            details_json={},
        )
        await self._session.commit()
        await self._session.refresh(suite_version)
        return suite_version

    async def patch_suite_version_manifest(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        patch: dict[str, Any],
    ) -> EvaluationSuiteVersion:
        suite_version = await self._require_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        if suite_version.status == "published":
            raise ValueError("published evaluation suite version manifest is immutable")
        suite_version.manifest_json = {**suite_version.manifest_json, **patch}
        suite_version.content_hash = self._digest(suite_version.manifest_json)
        await self._session.commit()
        await self._session.refresh(suite_version)
        return suite_version

    async def _require_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
    ) -> EvaluationSuiteVersion:
        suite_version = await self._repo.get_suite_version(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
        )
        if suite_version is None:
            raise ValueError("evaluation suite version not found")
        return suite_version

    async def _get_conversation_evaluation(
        self,
        *,
        tenant_id: str | UUID,
        evaluation_id: str | UUID,
    ) -> ConversationEvaluation:
        result = await self._session.execute(
            select(ConversationEvaluation).where(
                ConversationEvaluation.tenant_id == tenant_id,
                ConversationEvaluation.id == evaluation_id,
            )
        )
        evaluation = result.scalar_one_or_none()
        if evaluation is None:
            raise ValueError("conversation evaluation not found")
        return evaluation

    async def _get_skill_suggestion(
        self,
        *,
        tenant_id: str | UUID,
        suggestion_id: str | UUID,
    ) -> SkillSuggestion:
        result = await self._session.execute(
            select(SkillSuggestion).where(
                SkillSuggestion.tenant_id == tenant_id,
                SkillSuggestion.id == suggestion_id,
            )
        )
        suggestion = result.scalar_one_or_none()
        if suggestion is None:
            raise ValueError("skill suggestion not found")
        return suggestion

    async def _advisor_target_and_patch(
        self,
        *,
        tenant_id: str | UUID,
        suggestion: SkillSuggestion,
    ) -> tuple[str, str, str, dict[str, Any]]:
        if suggestion.suggestion_type not in {"edit", "new_skill", "clarification"}:
            raise ValueError("unsupported advisor suggestion type")
        if suggestion.suggestion_type == "edit":
            if suggestion.skill_id is None:
                raise ValueError("edit suggestion has no target skill")
            skill = await self._session.get(CustomSkill, suggestion.skill_id)
            if skill is None or str(skill.tenant_id) != str(tenant_id):
                raise ValueError("target skill not found")
            target_ref = f"custom_skill:{skill.id}"
            base_payload = {
                "id": str(skill.id),
                "name": skill.name,
                "description": skill.description,
                "instructions": skill.instructions,
                "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
            }
            base_version_ref = f"{target_ref}:current"
            patch = {
                "op": "replace",
                "path": "/instructions",
                "value": suggestion.proposed_instructions or (suggestion.patch or {}).get("after") or "",
                "legacy_patch": suggestion.patch or {},
            }
            return target_ref, base_version_ref, self._digest(base_payload), patch
        target_ref = f"custom_skill:new:{suggestion.id}"
        patch = {
            "op": "create",
            "path": "/custom_skills",
            "value": {
                "name": suggestion.title[:100],
                "description": suggestion.rationale[:500],
                "instructions": suggestion.proposed_instructions or suggestion.title,
            },
        }
        return target_ref, f"{target_ref}:draft", self._digest({"suggestion_id": str(suggestion.id)}), patch

    async def _validate_affected_cases(
        self,
        *,
        tenant_id: str | UUID,
        suite_version: EvaluationSuiteVersion | None,
        affected_case_ids: list[str | UUID],
    ) -> list[str | UUID]:
        if suite_version is None or not affected_case_ids:
            return affected_case_ids
        cases = await self._repo.list_cases_by_ids(
            tenant_id=tenant_id,
            suite_version_id=suite_version.id,
            case_ids=affected_case_ids,
        )
        found = {str(case.id) for case in cases}
        missing = sorted(str(case_id) for case_id in affected_case_ids if str(case_id) not in found)
        if missing:
            raise ValueError(f"affected cases not found in suite version: {missing}")
        return affected_case_ids

    @staticmethod
    def _advisor_suggestion_type(suggestion: SkillSuggestion) -> str:
        if suggestion.suggestion_type in {"edit", "new_skill", "clarification"}:
            return "instruction_skill"
        return str(suggestion.suggestion_type)

    async def _require_worker_run(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        worker_id: str,
    ) -> EvaluationRun:
        run = await self._repo.get_run(tenant_id=tenant_id, run_id=run_id)
        if run is None:
            raise ValueError("evaluation run not found")
        if run.status != "running" or run.lease_holder != worker_id:
            raise ValueError("evaluation run is not leased by this worker")
        return run

    @staticmethod
    def _run_gate_decision(run: EvaluationRun | None) -> str:
        if run is None:
            return "missing"
        summary = run.summary_json or {}
        gate_decision = summary.get("gate_decision")
        if gate_decision in {"passed", "failed", "canceled", "blocked"}:
            return str(gate_decision)
        if run.status == "passed":
            return "passed"
        return "failed"

    @staticmethod
    def _optional_uuid(value: str | UUID) -> UUID | None:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value))
        except ValueError:
            return None

    @staticmethod
    def _digest(payload: Any) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_target_kinds(target_kinds: list[str]) -> list[str]:
        normalized = sorted({str(kind) for kind in target_kinds if str(kind)})
        if not normalized:
            raise ValueError("evaluation suite requires at least one target kind")
        unsupported = sorted(set(normalized) - SUPPORTED_TARGET_KINDS)
        if unsupported:
            raise ValueError(f"unsupported evaluation target kinds: {unsupported}")
        return normalized

    @staticmethod
    def _default_gate_policy(gate_policy: dict[str, Any] | None) -> dict[str, Any]:
        payload = {
            "version": "gate-policy.v1",
            "security_hard_fail": True,
            "min_overall_pass_rate": 1.0,
            "max_new_regressions": 0,
            "require_manual_review_for": [],
            **(gate_policy or {}),
        }
        return sanitize_error_payload(payload)

    @staticmethod
    def _validate_case_import_payload(payload: dict[str, Any]) -> None:
        if not str(payload["case_key"]).strip():
            raise ValueError("case_key is required")
        if not str(payload["question"]).strip():
            raise ValueError("question is required")
        EvaluationService._validate_target_kinds([str(kind) for kind in payload.get("target_kinds") or []])
        operation = str(payload.get("operation") or "answer_question")
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError(f"unsupported evaluation operation: {operation}")
        EvaluationExpectedContract.model_validate(dict(payload.get("expected_contract") or {}))
