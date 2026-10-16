from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.evaluation import (
    AdvisorChangeSet,
    AdvisorSuggestion,
    EvaluationArtifact,
    EvaluationAssessment,
    EvaluationAuditEvent,
    EvaluationCase,
    EvaluationCaseRun,
    EvaluationRun,
    EvaluationSuite,
    EvaluationSuiteVersion,
    EvaluationTargetSnapshot,
    PromotionDecision,
)


def _has_no_preflight_blockers():
    return func.json_array_length(EvaluationRun.preflight_blockers_json) == 0


class EvaluationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
    ) -> EvaluationSuiteVersion | None:
        result = await self._session.execute(
            select(EvaluationSuiteVersion).where(
                EvaluationSuiteVersion.tenant_id == tenant_id,
                EvaluationSuiteVersion.id == suite_version_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_suite(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID,
    ) -> EvaluationSuite | None:
        result = await self._session.execute(
            select(EvaluationSuite).where(
                EvaluationSuite.tenant_id == tenant_id,
                EvaluationSuite.id == suite_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_suite_by_slug(
        self,
        *,
        tenant_id: str | UUID,
        slug: str,
    ) -> EvaluationSuite | None:
        result = await self._session.execute(
            select(EvaluationSuite).where(
                EvaluationSuite.tenant_id == tenant_id,
                EvaluationSuite.slug == slug,
            )
        )
        return result.scalar_one_or_none()

    async def list_suites(
        self,
        *,
        tenant_id: str | UUID,
        query: str = "",
        target_kind: str = "",
        status: str = "",
        limit: int = 50,
    ) -> list[EvaluationSuite]:
        statement = select(EvaluationSuite).where(EvaluationSuite.tenant_id == tenant_id)
        if status:
            statement = statement.where(EvaluationSuite.lifecycle == status)
        result = await self._session.execute(
            statement.order_by(EvaluationSuite.updated_at.desc(), EvaluationSuite.id).limit(max(1, limit))
        )
        suites = list(result.scalars().all())
        query_lower = query.lower().strip()
        if query_lower:
            suites = [
                suite
                for suite in suites
                if query_lower in suite.name.lower()
                or query_lower in suite.slug.lower()
                or query_lower in (suite.description or "").lower()
            ]
        if target_kind:
            suites = [suite for suite in suites if target_kind in (suite.target_kinds_json or [])]
        return suites

    async def list_suite_versions(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID,
    ) -> list[EvaluationSuiteVersion]:
        result = await self._session.execute(
            select(EvaluationSuiteVersion)
            .where(
                EvaluationSuiteVersion.tenant_id == tenant_id,
                EvaluationSuiteVersion.suite_id == suite_id,
            )
            .order_by(EvaluationSuiteVersion.version_num.desc())
        )
        return list(result.scalars().all())

    async def create_suite(
        self,
        *,
        tenant_id: str | UUID,
        slug: str,
        name: str,
        description: str,
        owner_id: str | UUID | None,
        target_kinds_json: list[str],
        lifecycle: str,
    ) -> EvaluationSuite:
        suite = EvaluationSuite(
            tenant_id=tenant_id,
            slug=slug,
            name=name,
            description=description,
            owner_id=owner_id,
            target_kinds_json=target_kinds_json,
            lifecycle=lifecycle,
        )
        self._session.add(suite)
        await self._session.flush()
        return suite

    async def create_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID,
        version_num: int,
        status: str,
        contract_version: str,
        manifest_json: dict,
        gate_policy_json: dict,
        case_count: int,
        content_hash: str,
        created_by: str | UUID | None,
    ) -> EvaluationSuiteVersion:
        version = EvaluationSuiteVersion(
            tenant_id=tenant_id,
            suite_id=suite_id,
            version_num=version_num,
            status=status,
            contract_version=contract_version,
            manifest_json=manifest_json,
            gate_policy_json=gate_policy_json,
            case_count=case_count,
            content_hash=content_hash,
            created_by=created_by,
        )
        self._session.add(version)
        await self._session.flush()
        return version

    async def get_run(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
    ) -> EvaluationRun | None:
        result = await self._session.execute(
            select(EvaluationRun).where(
                EvaluationRun.tenant_id == tenant_id,
                EvaluationRun.id == run_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_change_set(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
    ) -> AdvisorChangeSet | None:
        result = await self._session.execute(
            select(AdvisorChangeSet).where(
                AdvisorChangeSet.tenant_id == tenant_id,
                AdvisorChangeSet.id == change_set_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_change_sets_for_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        limit: int = 50,
    ) -> list[AdvisorChangeSet]:
        result = await self._session.execute(
            select(AdvisorChangeSet)
            .where(
                AdvisorChangeSet.tenant_id == tenant_id,
                AdvisorChangeSet.suite_version_id == suite_version_id,
            )
            .order_by(AdvisorChangeSet.created_at.desc(), AdvisorChangeSet.id)
            .limit(max(1, limit))
        )
        return list(result.scalars().all())

    async def get_change_set_for_legacy_skill_suggestion(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID | None,
        suggestion_id: str | UUID,
        target_ref: str,
    ) -> AdvisorChangeSet | None:
        statement = select(AdvisorChangeSet).where(
            AdvisorChangeSet.tenant_id == tenant_id,
            AdvisorChangeSet.target_ref == target_ref,
        )
        if suite_version_id is None:
            statement = statement.where(AdvisorChangeSet.suite_version_id.is_(None))
        else:
            statement = statement.where(AdvisorChangeSet.suite_version_id == suite_version_id)
        result = await self._session.execute(statement)
        for change_set in result.scalars().all():
            if str((change_set.evidence_json or {}).get("legacy_skill_suggestion_id")) == str(suggestion_id):
                return change_set
        return None

    async def list_advisor_suggestions_for_change_set(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
    ) -> list[AdvisorSuggestion]:
        result = await self._session.execute(
            select(AdvisorSuggestion)
            .where(
                AdvisorSuggestion.tenant_id == tenant_id,
                AdvisorSuggestion.change_set_id == change_set_id,
            )
            .order_by(AdvisorSuggestion.created_at, AdvisorSuggestion.id)
        )
        return list(result.scalars().all())

    async def list_promotion_decisions_for_change_set(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
    ) -> list[PromotionDecision]:
        result = await self._session.execute(
            select(PromotionDecision)
            .where(
                PromotionDecision.tenant_id == tenant_id,
                PromotionDecision.change_set_id == change_set_id,
            )
            .order_by(PromotionDecision.created_at.desc(), PromotionDecision.id)
        )
        return list(result.scalars().all())

    async def get_run_by_idempotency_key(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        idempotency_key: str,
    ) -> EvaluationRun | None:
        result = await self._session.execute(
            select(EvaluationRun).where(
                EvaluationRun.tenant_id == tenant_id,
                EvaluationRun.suite_version_id == suite_version_id,
                EvaluationRun.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_next_claimable_run(
        self,
        *,
        tenant_id: str | UUID,
        now: datetime,
    ) -> EvaluationRun | None:
        result = await self._session.execute(
            select(EvaluationRun)
            .where(
                EvaluationRun.tenant_id == tenant_id,
                _has_no_preflight_blockers(),
                (
                    (EvaluationRun.status == "queued")
                    | ((EvaluationRun.status == "running") & (EvaluationRun.lease_expires_at < now))
                ),
            )
            .order_by(EvaluationRun.created_at, EvaluationRun.id)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_cases_for_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
    ) -> list[EvaluationCase]:
        result = await self._session.execute(
            select(EvaluationCase)
            .where(
                EvaluationCase.tenant_id == tenant_id,
                EvaluationCase.suite_version_id == suite_version_id,
            )
            .order_by(EvaluationCase.case_key)
        )
        return list(result.scalars().all())

    async def list_runs_for_suite_version(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        limit: int = 50,
    ) -> list[EvaluationRun]:
        result = await self._session.execute(
            select(EvaluationRun)
            .where(
                EvaluationRun.tenant_id == tenant_id,
                EvaluationRun.suite_version_id == suite_version_id,
            )
            .order_by(EvaluationRun.created_at.desc(), EvaluationRun.id)
            .limit(max(1, limit))
        )
        return list(result.scalars().all())

    async def list_case_runs_for_run(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
    ) -> list[EvaluationCaseRun]:
        result = await self._session.execute(
            select(EvaluationCaseRun)
            .where(
                EvaluationCaseRun.tenant_id == tenant_id,
                EvaluationCaseRun.run_id == run_id,
            )
            .order_by(EvaluationCaseRun.created_at, EvaluationCaseRun.id)
        )
        return list(result.scalars().all())

    async def list_assessments_for_case_runs(
        self,
        *,
        tenant_id: str | UUID,
        case_run_ids: list[str | UUID],
    ) -> list[EvaluationAssessment]:
        if not case_run_ids:
            return []
        result = await self._session.execute(
            select(EvaluationAssessment)
            .where(
                EvaluationAssessment.tenant_id == tenant_id,
                EvaluationAssessment.case_run_id.in_(case_run_ids),
            )
            .order_by(EvaluationAssessment.created_at, EvaluationAssessment.id)
        )
        return list(result.scalars().all())

    async def get_case_by_key(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        case_key: str,
    ) -> EvaluationCase | None:
        result = await self._session.execute(
            select(EvaluationCase).where(
                EvaluationCase.tenant_id == tenant_id,
                EvaluationCase.suite_version_id == suite_version_id,
                EvaluationCase.case_key == case_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_cases_by_ids(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        case_ids: list[str | UUID],
    ) -> list[EvaluationCase]:
        if not case_ids:
            return []
        result = await self._session.execute(
            select(EvaluationCase).where(
                EvaluationCase.tenant_id == tenant_id,
                EvaluationCase.suite_version_id == suite_version_id,
                EvaluationCase.id.in_(case_ids),
            )
        )
        return list(result.scalars().all())

    async def create_case(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        case_key: str,
        title: str,
        target_kinds_json: list[str],
        operation: str,
        question: str,
        expected_contract_json: dict,
        provenance_json: dict,
        tags_json: list[str],
        content_hash: str,
        immutable: bool,
    ) -> EvaluationCase:
        case = EvaluationCase(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
            case_key=case_key,
            title=title,
            target_kinds_json=target_kinds_json,
            operation=operation,
            question=question,
            expected_contract_json=expected_contract_json,
            provenance_json=provenance_json,
            tags_json=tags_json,
            content_hash=content_hash,
            immutable=immutable,
        )
        self._session.add(case)
        await self._session.flush()
        return case

    async def create_target_snapshot(
        self,
        *,
        tenant_id: str | UUID,
        target_kind: str,
        target_ref: str,
        contract_version: str,
        snapshot_json: dict,
        pin_digest: str,
        blockers_json: list[str],
    ) -> EvaluationTargetSnapshot:
        snapshot = EvaluationTargetSnapshot(
            tenant_id=tenant_id,
            target_kind=target_kind,
            target_ref=target_ref,
            contract_version=contract_version,
            snapshot_json=snapshot_json,
            pin_digest=pin_digest,
            blockers_json=blockers_json,
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def create_run(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID,
        target_snapshot_id: str | UUID,
        status: str,
        actor_type: str,
        actor_id: str,
        idempotency_key: str | None,
        preflight_blockers_json: list[str],
    ) -> EvaluationRun:
        run = EvaluationRun(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
            target_snapshot_id=target_snapshot_id,
            status=status,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            preflight_blockers_json=preflight_blockers_json,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def create_case_run(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID,
        case_id: str | UUID,
        status: str,
        attempt: int,
        input_digest: str,
        output_digest: str,
        result_json: dict,
        error_json: dict,
        immutable: bool,
        started_at: datetime,
        completed_at: datetime,
    ) -> EvaluationCaseRun:
        case_run = EvaluationCaseRun(
            tenant_id=tenant_id,
            run_id=run_id,
            case_id=case_id,
            status=status,
            attempt=attempt,
            input_digest=input_digest,
            output_digest=output_digest,
            result_json=result_json,
            error_json=error_json,
            immutable=immutable,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._session.add(case_run)
        await self._session.flush()
        return case_run

    async def create_assessment(
        self,
        *,
        tenant_id: str | UUID,
        case_run_id: str | UUID,
        category: str,
        status: str,
        score: str | None,
        hard_fail: bool,
        details_json: dict,
    ) -> EvaluationAssessment:
        assessment = EvaluationAssessment(
            tenant_id=tenant_id,
            case_run_id=case_run_id,
            category=category,
            status=status,
            score=score,
            hard_fail=hard_fail,
            details_json=details_json,
            immutable=True,
        )
        self._session.add(assessment)
        await self._session.flush()
        return assessment

    async def create_artifact(
        self,
        *,
        tenant_id: str | UUID,
        run_id: str | UUID | None,
        case_run_id: str | UUID | None,
        artifact_type: str,
        uri: str,
        content_hash: str,
        metadata_json: dict,
    ) -> EvaluationArtifact:
        artifact = EvaluationArtifact(
            tenant_id=tenant_id,
            run_id=run_id,
            case_run_id=case_run_id,
            artifact_type=artifact_type,
            uri=uri,
            content_hash=content_hash,
            metadata_json=metadata_json,
            immutable=True,
        )
        self._session.add(artifact)
        await self._session.flush()
        return artifact

    async def create_promotion_decision(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID | None,
        verification_run_id: str | UUID | None,
        regression_run_id: str | UUID | None,
        decision: str,
        decided_by: str | UUID | None,
        rationale: str,
        audit_json: dict,
    ) -> PromotionDecision:
        promotion = PromotionDecision(
            tenant_id=tenant_id,
            change_set_id=change_set_id,
            verification_run_id=verification_run_id,
            regression_run_id=regression_run_id,
            decision=decision,
            decided_by=decided_by,
            rationale=rationale,
            audit_json=audit_json,
        )
        self._session.add(promotion)
        await self._session.flush()
        return promotion

    async def create_advisor_change_set(
        self,
        *,
        tenant_id: str | UUID,
        suite_version_id: str | UUID | None,
        target_ref: str,
        base_version_ref: str,
        base_etag: str,
        status: str,
        evidence_json: dict,
        created_by: str,
    ) -> AdvisorChangeSet:
        change_set = AdvisorChangeSet(
            tenant_id=tenant_id,
            suite_version_id=suite_version_id,
            target_ref=target_ref,
            base_version_ref=base_version_ref,
            base_etag=base_etag,
            status=status,
            evidence_json=evidence_json,
            created_by=created_by,
        )
        self._session.add(change_set)
        await self._session.flush()
        return change_set

    async def create_advisor_suggestion(
        self,
        *,
        tenant_id: str | UUID,
        change_set_id: str | UUID,
        suggestion_type: str,
        patch_json: dict,
        affected_case_ids_json: list[str],
        status: str,
    ) -> AdvisorSuggestion:
        suggestion = AdvisorSuggestion(
            tenant_id=tenant_id,
            change_set_id=change_set_id,
            suggestion_type=suggestion_type,
            patch_json=patch_json,
            affected_case_ids_json=affected_case_ids_json,
            status=status,
        )
        self._session.add(suggestion)
        await self._session.flush()
        return suggestion

    async def create_audit_event(
        self,
        *,
        tenant_id: str | UUID,
        suite_id: str | UUID | None,
        suite_version_id: str | UUID | None,
        run_id: str | UUID | None,
        actor_type: str,
        actor_id: str,
        action: str,
        outcome: str,
        details_json: dict,
    ) -> EvaluationAuditEvent:
        event = EvaluationAuditEvent(
            tenant_id=tenant_id,
            suite_id=suite_id,
            suite_version_id=suite_version_id,
            run_id=run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            details_json=details_json,
        )
        self._session.add(event)
        await self._session.flush()
        return event
