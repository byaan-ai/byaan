from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.semantic_models import SemanticModel, SemanticModelAuditEvent
from server.models.source_resources import SourceResource
from server.models.source_snapshots import SourceSnapshot
from server.schemas.semantic_models import SemanticModelCreate, SemanticModelPatch


class SemanticModelService:
    @staticmethod
    async def create_model(
        *,
        session: AsyncSession,
        tenant_id: UUID,
        user_id: UUID | None,
        payload: SemanticModelCreate,
    ) -> SemanticModel:
        slug = payload.slug or SemanticModelService._slugify(payload.name)
        await SemanticModelService._validate_sources(
            session=session,
            tenant_id=tenant_id,
            source_resource_ids=payload.source_resource_ids,
            source_snapshot_ids=payload.source_snapshot_ids,
        )
        model = SemanticModel(
            tenant_id=tenant_id,
            created_by=user_id,
            slug=slug,
            name=payload.name,
            domain=payload.domain,
            owner=payload.owner,
            description=payload.description,
            datasource_id=payload.datasource_id,
            datasource_name=payload.datasource_name,
            datasource_kind=payload.datasource_kind,
            manifest_json=SemanticModelService._normalize_manifest(payload.manifest),
            source_resource_ids_json=[str(item) for item in payload.source_resource_ids],
            source_snapshot_ids_json=[str(item) for item in payload.source_snapshot_ids],
            status="beta",
            readiness=20,
            readiness_level="partial",
            validation_result_json=SemanticModelService._partial_validation(
                source_snapshot_ids=[str(item) for item in payload.source_snapshot_ids],
                manifest=payload.manifest,
            ),
            consumer_summary_json=payload.consumer_summary,
        )
        session.add(model)
        await session.flush()
        session.add(
            SemanticModelAuditEvent(
                tenant_id=tenant_id,
                model_id=model.id,
                actor_id=user_id,
                action="semantic_model.beta.create",
                details_json={"commercial_status": "PARTIAL"},
            )
        )
        await session.commit()
        await session.refresh(model)
        return model

    @staticmethod
    async def list_models(*, session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
        result = await session.execute(
            select(SemanticModel).where(SemanticModel.tenant_id == tenant_id).order_by(SemanticModel.updated_at.desc())
        )
        return [SemanticModelService.model_to_payload(model) for model in result.scalars().all()]

    @staticmethod
    async def get_model(*, session: AsyncSession, tenant_id: UUID, slug: str) -> SemanticModel | None:
        return await session.scalar(
            select(SemanticModel).where(SemanticModel.tenant_id == tenant_id, SemanticModel.slug == slug)
        )

    @staticmethod
    async def update_model(
        *,
        session: AsyncSession,
        tenant_id: UUID,
        slug: str,
        user_id: UUID | None,
        payload: SemanticModelPatch,
    ) -> SemanticModel | None:
        model = await SemanticModelService.get_model(session=session, tenant_id=tenant_id, slug=slug)
        if model is None:
            return None
        expected_revision = payload.revision_guard()
        if expected_revision is None:
            raise ValueError("expected_revision is required for semantic model updates")
        if expected_revision != model.revision:
            raise RuntimeError(f"Semantic Model revision conflict: current revision is {model.revision}")

        next_source_resource_ids = (
            [str(item) for item in payload.source_resource_ids]
            if payload.source_resource_ids is not None
            else list(model.source_resource_ids_json or [])
        )
        next_source_snapshot_ids = (
            [str(item) for item in payload.source_snapshot_ids]
            if payload.source_snapshot_ids is not None
            else list(model.source_snapshot_ids_json or [])
        )
        await SemanticModelService._validate_sources(
            session=session,
            tenant_id=tenant_id,
            source_resource_ids=[UUID(item) for item in next_source_resource_ids],
            source_snapshot_ids=[UUID(item) for item in next_source_snapshot_ids],
        )

        for field in ("name", "domain", "owner", "description", "datasource_id", "datasource_name", "datasource_kind"):
            value = getattr(payload, field)
            if value is not None:
                setattr(model, field, value)
        if payload.manifest is not None:
            model.manifest_json = SemanticModelService._normalize_manifest(payload.manifest)
        if payload.source_resource_ids is not None:
            model.source_resource_ids_json = next_source_resource_ids
        if payload.source_snapshot_ids is not None:
            model.source_snapshot_ids_json = next_source_snapshot_ids
        if payload.consumer_summary is not None:
            model.consumer_summary_json = payload.consumer_summary

        model.revision += 1
        model.draft_revision = f"draft-{model.revision}"
        model.status = "beta"
        model.readiness = min(model.readiness or 20, 40)
        model.readiness_level = "partial"
        model.validation_result_json = SemanticModelService._partial_validation(
            source_snapshot_ids=list(model.source_snapshot_ids_json or []),
            manifest=dict(model.manifest_json or {}),
        )
        session.add(
            SemanticModelAuditEvent(
                tenant_id=tenant_id,
                model_id=model.id,
                actor_id=user_id,
                action="semantic_model.beta.update",
                details_json={"revision": model.revision, "commercial_status": "PARTIAL"},
            )
        )
        await session.commit()
        await session.refresh(model)
        return model

    @staticmethod
    async def validate_model(
        *,
        session: AsyncSession,
        tenant_id: UUID,
        slug: str,
        user_id: UUID | None,
    ) -> SemanticModel | None:
        model = await SemanticModelService.get_model(session=session, tenant_id=tenant_id, slug=slug)
        if model is None:
            return None
        validation = SemanticModelService._partial_validation(
            source_snapshot_ids=list(model.source_snapshot_ids_json or []),
            manifest=dict(model.manifest_json or {}),
        )
        model.validation_result_json = validation
        model.readiness = int(validation["score"])
        model.readiness_level = "partial"
        model.status = "needs_review" if validation["warnings"] else "beta"
        session.add(
            SemanticModelAuditEvent(
                tenant_id=tenant_id,
                model_id=model.id,
                actor_id=user_id,
                action="semantic_model.beta.validate",
                details_json=validation,
            )
        )
        await session.commit()
        await session.refresh(model)
        return model

    @staticmethod
    async def publish_model(*, session: AsyncSession, tenant_id: UUID, slug: str) -> SemanticModel | None:
        model = await SemanticModelService.get_model(session=session, tenant_id=tenant_id, slug=slug)
        if model is None:
            return None
        raise RuntimeError(
            "Semantic Model publish is blocked in the official beta until runtime execution credentials, "
            "source provenance, and maintainer-reviewed validation are certified."
        )

    @staticmethod
    async def query_metric(*, session: AsyncSession, tenant_id: UUID, slug: str) -> SemanticModel | None:
        model = await SemanticModelService.get_model(session=session, tenant_id=tenant_id, slug=slug)
        if model is None:
            return None
        raise RuntimeError("Semantic metric execution is beta-blocked in this official landing batch.")

    @staticmethod
    def model_to_payload(model: SemanticModel) -> dict[str, Any]:
        validation = dict(model.validation_result_json or {})
        return {
            "id": model.slug,
            "uuid": model.id,
            "slug": model.slug,
            "name": model.name,
            "domain": model.domain,
            "owner": model.owner,
            "description": model.description,
            "datasourceId": model.datasource_id,
            "datasourceName": model.datasource_name,
            "datasourceKind": model.datasource_kind,
            "contractVersion": model.contract_version,
            "manifest": model.manifest_json,
            "sourceResourceIds": model.source_resource_ids_json,
            "sourceSnapshotIds": model.source_snapshot_ids_json,
            "status": model.status,
            "commercialStatus": "PARTIAL",
            "runtimeStatus": "not_certified",
            "revision": model.revision,
            "draftRevision": model.draft_revision,
            "publishedVersion": model.published_version,
            "readiness": model.readiness,
            "readinessLevel": model.readiness_level,
            "readinessDetail": validation,
            "consumerSummary": model.consumer_summary_json,
            "createdBy": model.created_by,
            "createdAt": model.created_at,
            "updatedAt": model.updated_at,
        }

    @staticmethod
    async def _validate_sources(
        *,
        session: AsyncSession,
        tenant_id: UUID,
        source_resource_ids: list[UUID],
        source_snapshot_ids: list[UUID],
    ) -> None:
        if source_resource_ids:
            result = await session.execute(
                select(SourceResource.id).where(
                    SourceResource.tenant_id == tenant_id,
                    SourceResource.id.in_(source_resource_ids),
                )
            )
            found = {str(item) for item in result.scalars().all()}
            missing = {str(item) for item in source_resource_ids} - found
            if missing:
                raise ValueError("Source resource not found")
        if source_snapshot_ids:
            result = await session.execute(
                select(SourceSnapshot.id).where(
                    SourceSnapshot.tenant_id == tenant_id,
                    SourceSnapshot.id.in_(source_snapshot_ids),
                )
            )
            found = {str(item) for item in result.scalars().all()}
            missing = {str(item) for item in source_snapshot_ids} - found
            if missing:
                raise ValueError("Source snapshot not found")

    @staticmethod
    def _partial_validation(*, source_snapshot_ids: list[str], manifest: dict[str, Any]) -> dict[str, Any]:
        metrics = manifest.get("metrics") if isinstance(manifest.get("metrics"), list) else []
        entities = manifest.get("entities") if isinstance(manifest.get("entities"), list) else []
        warnings: list[str] = []
        if not source_snapshot_ids:
            warnings.append("No immutable Source snapshot provenance is attached.")
        if not entities:
            warnings.append("No reviewed semantic entities are attached.")
        if not metrics:
            warnings.append("No reviewed metrics are attached.")
        blockers = [
            "Official semantic modeling runtime is beta/PARTIAL; publish and query execution are blocked until certified.",
        ]
        score = 40 if source_snapshot_ids and entities and metrics else 20
        return {
            "valid": False,
            "status": "PARTIAL",
            "score": score,
            "level": "partial",
            "blockers": blockers,
            "warnings": warnings,
            "validated_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def _normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": manifest.get("schema_version")
            or manifest.get("schemaVersion")
            or "semantic.model.v1beta",
            "entities": manifest.get("entities") if isinstance(manifest.get("entities"), list) else [],
            "relationships": manifest.get("relationships") if isinstance(manifest.get("relationships"), list) else [],
            "metrics": manifest.get("metrics") if isinstance(manifest.get("metrics"), list) else [],
            "dimensions": manifest.get("dimensions") if isinstance(manifest.get("dimensions"), list) else [],
            "policy": manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {},
            "provenance": manifest.get("provenance") if isinstance(manifest.get("provenance"), dict) else {},
        }

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        return slug or "semantic-model"

    @staticmethod
    def _content_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
