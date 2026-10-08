from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from server.models.dashboard import Dashboard
from server.models.folder_dashboard import FolderDashboard
from server.models.sharing import (
    SharingAuditEvent,
    SharingCompatibilityLink,
    SharingGrant,
    SharingSecret,
    SharingViewerSession,
)
from server.services.viewer_session_service import ViewerSessionService
from server.utils.error_sanitizer import sanitize_error_payload

_SECRET_ALGORITHM = "pbkdf2_sha256:210000"


class SharingService:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create_dashboard_public_link(
        self,
        *,
        tenant_id: str | UUID,
        actor_id: str | UUID,
        dashboard_id: str | UUID,
        password: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SharingGrant:
        tenant_uuid = _coerce_uuid(tenant_id)
        actor_uuid = _coerce_uuid(actor_id)
        dashboard = await self._require_dashboard(tenant_id=tenant_uuid, dashboard_id=_coerce_uuid(dashboard_id))
        version_digest = _dashboard_digest(dashboard)
        grant = SharingGrant(
            tenant_id=tenant_uuid,
            object_type="dashboard",
            object_id=dashboard.id,
            object_version_id=dashboard.id,
            object_version_digest=version_digest,
            mode="immutable_version",
            channel="public_link",
            audience="link_holder",
            status="active",
            created_by=actor_uuid,
            metadata_json=sanitize_error_payload(metadata or {}),
        )
        self._session.add(grant)
        await self._session.flush()
        if password:
            self._session.add(
                self._build_secret(
                    tenant_id=tenant_uuid,
                    grant_id=grant.id,
                    actor_id=actor_uuid,
                    secret=password,
                )
            )
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                actor_id=str(actor_uuid),
                action="sharing.grant.create",
                outcome="active",
                details={"metadata": metadata or {}, "has_password": bool(password)},
            )
        )
        await self._session.commit()
        await self._session.refresh(grant)
        return grant

    async def ensure_folder_dashboard_grant(
        self,
        *,
        tenant_id: str | UUID,
        actor_id: str | UUID,
        folder_dashboard_id: str | UUID,
        dashboard_id: str | UUID,
    ) -> SharingGrant:
        tenant_uuid = _coerce_uuid(tenant_id)
        actor_uuid = _coerce_uuid(actor_id)
        folder_dashboard_uuid = _coerce_uuid(folder_dashboard_id)
        legacy_id = str(folder_dashboard_uuid)
        existing = await self._grant_for_legacy_link("folder_dashboard", legacy_id)
        dashboard = await self._require_dashboard(tenant_id=tenant_uuid, dashboard_id=_coerce_uuid(dashboard_id))
        version_digest = _dashboard_digest(dashboard)
        metadata = sanitize_error_payload(
            {"legacy_surface": "folder_dashboard", "legacy_id": legacy_id, "dashboard_id": str(dashboard.id)}
        )
        if existing is not None:
            existing.object_id = dashboard.id
            existing.object_version_id = dashboard.id
            existing.object_version_digest = version_digest
            existing.status = "active"
            existing.revoked_at = None
            existing.revoked_by = None
            existing.revocation_reason = None
            existing.metadata_json = metadata
            link = await self._compatibility_link_for_grant(
                grant_id=existing.id,
                legacy_surface="folder_dashboard",
                legacy_id=legacy_id,
            )
            if link is not None:
                link.metadata_json = metadata
            await self._session.flush()
            grant = existing
        else:
            grant = SharingGrant(
                tenant_id=tenant_uuid,
                object_type="dashboard",
                object_id=dashboard.id,
                object_version_id=dashboard.id,
                object_version_digest=version_digest,
                mode="immutable_version",
                channel="folder",
                audience="folder_member",
                status="active",
                created_by=actor_uuid,
                metadata_json=metadata,
            )
            self._session.add(grant)
            await self._session.flush()
            self._session.add(
                SharingCompatibilityLink(
                    tenant_id=tenant_uuid,
                    grant_id=grant.id,
                    legacy_surface="folder_dashboard",
                    legacy_id=legacy_id,
                    metadata_json=metadata,
                )
            )
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                actor_id=str(actor_uuid),
                action="sharing.compat.folder_dashboard.upsert",
                outcome="active",
                details=metadata,
            )
        )
        await self._session.commit()
        await self._session.refresh(grant)
        return grant

    async def ensure_notebook_worker_grant(
        self,
        *,
        tenant_id: str | UUID,
        actor_id: str | UUID,
        notebook_id: str | UUID,
        legacy_surface: str,
        legacy_id: str,
        has_password: bool = False,
        password: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SharingGrant:
        tenant_uuid = _coerce_uuid(tenant_id)
        actor_uuid = _coerce_uuid(actor_id)
        notebook_uuid = _coerce_uuid(notebook_id)
        existing = await self._grant_for_legacy_link(legacy_surface, legacy_id)
        grant_metadata = sanitize_error_payload(
            {
                "legacy_surface": legacy_surface,
                "legacy_id": legacy_id,
                "has_password": has_password,
                **(metadata or {}),
            }
        )
        if existing is not None:
            existing.object_id = notebook_uuid
            existing.object_version_id = None
            existing.object_version_digest = ""
            existing.status = "active"
            existing.revoked_at = None
            existing.revoked_by = None
            existing.revocation_reason = None
            existing.metadata_json = grant_metadata
            grant = existing
        else:
            grant = SharingGrant(
                tenant_id=tenant_uuid,
                object_type="notebook",
                object_id=notebook_uuid,
                object_version_id=None,
                object_version_digest="",
                mode="live_notebook_export",
                channel="worker",
                audience="link_holder",
                status="active",
                created_by=actor_uuid,
                metadata_json=grant_metadata,
            )
            self._session.add(grant)
            await self._session.flush()
            self._session.add(
                SharingCompatibilityLink(
                    tenant_id=tenant_uuid,
                    grant_id=grant.id,
                    legacy_surface=legacy_surface,
                    legacy_id=legacy_id,
                    metadata_json=sanitize_error_payload({"notebook_id": str(notebook_uuid), **(metadata or {})}),
                )
            )
        await self._replace_password_secret(
            tenant_id=tenant_uuid,
            grant_id=grant.id,
            actor_id=actor_uuid,
            password=password,
            has_password=has_password,
        )
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                actor_id=str(actor_uuid),
                action="sharing.compat.notebook_worker.upsert",
                outcome="active",
                details={
                    "legacy_surface": legacy_surface,
                    "legacy_id": legacy_id,
                    "notebook_id": str(notebook_uuid),
                    "has_password": has_password,
                },
            )
        )
        await self._session.commit()
        await self._session.refresh(grant)
        return grant

    async def ensure_folder_notebook_grant(
        self,
        *,
        tenant_id: str | UUID,
        actor_id: str | UUID,
        folder_notebook_id: str | UUID,
        notebook_id: str | UUID,
        is_snapshot: bool,
        snapshot_updated_at: datetime | None = None,
    ) -> SharingGrant:
        tenant_uuid = _coerce_uuid(tenant_id)
        actor_uuid = _coerce_uuid(actor_id)
        folder_notebook_uuid = _coerce_uuid(folder_notebook_id)
        notebook_uuid = _coerce_uuid(notebook_id)
        legacy_id = str(folder_notebook_uuid)
        mode = "snapshot_export" if is_snapshot else "live_notebook"
        metadata = sanitize_error_payload(
            {
                "legacy_surface": "folder_notebook",
                "legacy_id": legacy_id,
                "notebook_id": str(notebook_uuid),
                "is_snapshot": is_snapshot,
                "snapshot_updated_at": snapshot_updated_at.isoformat() if snapshot_updated_at else None,
            }
        )
        existing = await self._grant_for_legacy_link("folder_notebook", legacy_id)
        if existing is not None:
            existing.object_id = notebook_uuid
            existing.object_version_id = None
            existing.object_version_digest = ""
            existing.mode = mode
            existing.status = "active"
            existing.revoked_at = None
            existing.revoked_by = None
            existing.revocation_reason = None
            existing.metadata_json = metadata
            link = await self._compatibility_link_for_grant(
                grant_id=existing.id,
                legacy_surface="folder_notebook",
                legacy_id=legacy_id,
            )
            if link is not None:
                link.metadata_json = metadata
            grant = existing
        else:
            grant = SharingGrant(
                tenant_id=tenant_uuid,
                object_type="notebook",
                object_id=notebook_uuid,
                object_version_id=None,
                object_version_digest="",
                mode=mode,
                channel="folder",
                audience="folder_member",
                status="active",
                created_by=actor_uuid,
                metadata_json=metadata,
            )
            self._session.add(grant)
            await self._session.flush()
            self._session.add(
                SharingCompatibilityLink(
                    tenant_id=tenant_uuid,
                    grant_id=grant.id,
                    legacy_surface="folder_notebook",
                    legacy_id=legacy_id,
                    metadata_json=metadata,
                )
            )
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                actor_id=str(actor_uuid),
                action="sharing.compat.folder_notebook.upsert",
                outcome="active",
                details=metadata,
            )
        )
        await self._session.commit()
        await self._session.refresh(grant)
        return grant

    async def list_grants(
        self,
        *,
        tenant_id: str | UUID,
        object_type: str | None = None,
        object_id: str | UUID | None = None,
        legacy_surface: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SharingGrant]:
        tenant_uuid = _coerce_uuid(tenant_id)
        query = select(SharingGrant).where(SharingGrant.tenant_id == tenant_uuid)
        if legacy_surface:
            query = query.join(SharingCompatibilityLink, SharingCompatibilityLink.grant_id == SharingGrant.id).where(
                SharingCompatibilityLink.legacy_surface == legacy_surface
            )
        if object_type:
            query = query.where(SharingGrant.object_type == object_type)
        if object_id is not None:
            query = query.where(SharingGrant.object_id == _coerce_uuid(object_id))
        if status:
            query = query.where(SharingGrant.status == status)
        query = query.order_by(SharingGrant.created_at.desc()).limit(max(1, min(limit, 100)))
        result = await self._session.execute(query)
        return list(result.scalars().unique().all())

    async def grant_evidence(self, *, tenant_id: str | UUID, grant_id: str | UUID) -> dict[str, Any]:
        tenant_uuid = _coerce_uuid(tenant_id)
        grant_uuid = _coerce_uuid(grant_id)
        grant = await self._session.scalar(
            select(SharingGrant).where(SharingGrant.tenant_id == tenant_uuid, SharingGrant.id == grant_uuid)
        )
        if grant is None:
            raise ValueError("sharing grant not found")
        link_result = await self._session.execute(
            select(SharingCompatibilityLink).where(
                SharingCompatibilityLink.tenant_id == tenant_uuid,
                SharingCompatibilityLink.grant_id == grant.id,
            )
        )
        secret_counts = (
            await self._session.execute(
                select(SharingSecret.secret_type, SharingSecret.status, func.count(SharingSecret.id))
                .where(SharingSecret.tenant_id == tenant_uuid, SharingSecret.grant_id == grant.id)
                .group_by(SharingSecret.secret_type, SharingSecret.status)
            )
        ).all()
        viewer_count = await self._session.scalar(
            select(func.count(SharingViewerSession.id)).where(
                SharingViewerSession.tenant_id == tenant_uuid,
                SharingViewerSession.grant_id == grant.id,
                SharingViewerSession.revoked_at.is_(None),
            )
        )
        audit_result = await self._session.execute(
            select(SharingAuditEvent)
            .where(SharingAuditEvent.tenant_id == tenant_uuid, SharingAuditEvent.grant_id == grant.id)
            .order_by(SharingAuditEvent.created_at.desc())
            .limit(10)
        )
        return {
            "grant": grant,
            "compatibility_links": list(link_result.scalars().all()),
            "secret_counts": [
                {"secret_type": secret_type, "status": secret_status, "count": count}
                for secret_type, secret_status, count in secret_counts
            ],
            "active_viewer_session_count": int(viewer_count or 0),
            "audit_events": list(audit_result.scalars().all()),
        }

    async def revoke_legacy_grant(
        self,
        *,
        tenant_id: str | UUID,
        legacy_surface: str,
        legacy_id: str,
        actor_id: str | UUID,
        reason: str,
    ) -> SharingGrant | None:
        grant = await self._grant_for_legacy_link(legacy_surface, legacy_id)
        if grant is None or grant.tenant_id != _coerce_uuid(tenant_id):
            return None
        return await self.revoke_grant(
            tenant_id=tenant_id,
            grant_id=grant.id,
            actor_id=actor_id,
            reason=reason,
        )

    async def verify_grant_secret(self, *, grant_id: str | UUID, secret: str) -> bool:
        grant_uuid = _coerce_uuid(grant_id)
        result = await self._session.execute(
            select(SharingSecret).where(
                SharingSecret.grant_id == grant_uuid,
                SharingSecret.secret_type == "password",
                SharingSecret.status == "active",
            )
        )
        saved = result.scalars().first()
        if saved is None:
            return False
        return hmac.compare_digest(saved.verifier_hash, _hash_secret(secret=secret, salt=saved.salt))

    async def issue_viewer_session(
        self,
        *,
        tenant_id: str | UUID,
        grant_id: str | UUID,
        viewer_user_id: str | UUID | None = None,
        principal: dict[str, Any] | None = None,
    ) -> tuple[str, SharingViewerSession]:
        tenant_uuid = _coerce_uuid(tenant_id)
        grant_uuid = _coerce_uuid(grant_id)
        grant = await self._require_active_grant(tenant_id=tenant_uuid, grant_id=grant_uuid)
        viewer_uuid = _coerce_uuid(viewer_user_id) if viewer_user_id is not None else None
        token = ViewerSessionService.generate_token(
            user_id=viewer_uuid or grant.created_by or tenant_uuid,
            tenant_id=tenant_uuid,
            grant_id=grant.id,
            asset_id=grant.object_id,
            version_id=grant.object_version_id,
        )
        payload = ViewerSessionService.verify(token)
        if payload is None:
            raise ValueError("viewer session token could not be verified")
        now = self._now()
        expires_at = datetime.fromtimestamp(int(payload["exp"]), UTC).replace(tzinfo=None)
        viewer_session = SharingViewerSession(
            tenant_id=tenant_uuid,
            grant_id=grant.id,
            object_type=grant.object_type,
            object_id=grant.object_id,
            object_version_id=grant.object_version_id,
            token_id=str(payload["jti"]),
            token_digest=_digest_text(token),
            viewer_user_id=viewer_uuid,
            viewer_principal_json=sanitize_error_payload(principal or {}),
            issued_at=now,
            expires_at=expires_at,
        )
        self._session.add(viewer_session)
        await self._session.flush()
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                viewer_session_id=viewer_session.id,
                actor_id=str(viewer_uuid or "anonymous"),
                action="sharing.viewer_session.issue",
                outcome="issued",
                details={"principal": principal or {}},
            )
        )
        await self._session.commit()
        await self._session.refresh(viewer_session)
        return token, viewer_session

    async def require_viewer_session(
        self,
        *,
        token: str,
        grant_id: str | UUID,
        object_id: str | UUID,
        object_version_id: str | UUID | None = None,
    ) -> SharingViewerSession | None:
        payload = ViewerSessionService.verify(token)
        if payload is None:
            return None
        grant_uuid = _coerce_uuid(grant_id)
        object_uuid = _coerce_uuid(object_id)
        version_uuid = _coerce_uuid(object_version_id) if object_version_id is not None else None
        if payload.get("grant_id") != str(grant_uuid):
            return None
        if payload.get("asset_id") != str(object_uuid):
            return None
        if version_uuid is not None and payload.get("version_id") != str(version_uuid):
            return None
        result = await self._session.execute(
            select(SharingViewerSession, SharingGrant)
            .join(SharingGrant, SharingGrant.id == SharingViewerSession.grant_id)
            .where(
                SharingViewerSession.token_digest == _digest_text(token),
                SharingViewerSession.grant_id == grant_uuid,
                SharingViewerSession.object_id == object_uuid,
                SharingViewerSession.object_version_id == version_uuid,
                SharingViewerSession.revoked_at.is_(None),
                SharingViewerSession.expires_at > self._now(),
                SharingGrant.status == "active",
                SharingGrant.revoked_at.is_(None),
            )
        )
        row = result.first()
        return row[0] if row else None

    async def require_viewer_session_for_dashboard(
        self,
        *,
        token: str,
        dashboard_id: str | UUID,
    ) -> tuple[UUID, SharingViewerSession] | None:
        payload = ViewerSessionService.verify(token)
        if payload is None or not payload.get("uid") or not payload.get("grant_id"):
            return None
        dashboard = await self._dashboard_for_viewer_payload(payload=payload, dashboard_id=_coerce_uuid(dashboard_id))
        if dashboard is None:
            return None
        viewer_session = await self.require_viewer_session(
            token=token,
            grant_id=payload["grant_id"],
            object_id=dashboard.id,
            object_version_id=dashboard.id,
        )
        if viewer_session is None:
            return None
        if not await self._legacy_folder_dashboard_is_active(viewer_session=viewer_session):
            return None
        return _coerce_uuid(payload["uid"]), viewer_session

    async def revoke_grant(
        self,
        *,
        tenant_id: str | UUID,
        grant_id: str | UUID,
        actor_id: str | UUID,
        reason: str,
    ) -> SharingGrant:
        tenant_uuid = _coerce_uuid(tenant_id)
        actor_uuid = _coerce_uuid(actor_id)
        grant = await self._require_active_grant(tenant_id=tenant_uuid, grant_id=_coerce_uuid(grant_id))
        now = self._now()
        grant.status = "revoked"
        grant.revoked_at = now
        grant.revoked_by = actor_uuid
        grant.revocation_reason = reason
        result = await self._session.execute(
            select(SharingViewerSession).where(
                SharingViewerSession.grant_id == grant.id,
                SharingViewerSession.revoked_at.is_(None),
            )
        )
        for viewer_session in result.scalars():
            viewer_session.revoked_at = now
        self._session.add(
            self._audit_event(
                tenant_id=tenant_uuid,
                grant=grant,
                actor_id=str(actor_uuid),
                action="sharing.grant.revoke",
                outcome="revoked",
                details={"reason": reason},
            )
        )
        await self._session.commit()
        await self._session.refresh(grant)
        return grant

    async def _require_dashboard(self, *, tenant_id: UUID, dashboard_id: UUID) -> Dashboard:
        result = await self._session.execute(
            select(Dashboard).where(
                Dashboard.tenant_id == tenant_id,
                Dashboard.id == dashboard_id,
            )
        )
        dashboard = result.scalars().first()
        if dashboard is None:
            raise ValueError("dashboard not found")
        return dashboard

    async def _grant_for_legacy_link(self, legacy_surface: str, legacy_id: str) -> SharingGrant | None:
        result = await self._session.execute(
            select(SharingGrant)
            .join(SharingCompatibilityLink, SharingCompatibilityLink.grant_id == SharingGrant.id)
            .where(
                SharingCompatibilityLink.legacy_surface == legacy_surface,
                SharingCompatibilityLink.legacy_id == legacy_id,
            )
        )
        return result.scalars().first()

    async def _compatibility_link_for_grant(
        self,
        *,
        grant_id: UUID,
        legacy_surface: str,
        legacy_id: str,
    ) -> SharingCompatibilityLink | None:
        result = await self._session.execute(
            select(SharingCompatibilityLink).where(
                SharingCompatibilityLink.grant_id == grant_id,
                SharingCompatibilityLink.legacy_surface == legacy_surface,
                SharingCompatibilityLink.legacy_id == legacy_id,
            )
        )
        return result.scalars().first()

    async def _dashboard_for_viewer_payload(self, *, payload: dict[str, Any], dashboard_id: UUID) -> Dashboard | None:
        try:
            tenant_id = _coerce_uuid(payload["tid"])
            version_id = _coerce_uuid(payload["version_id"])
        except (KeyError, ValueError):
            return None
        if version_id != dashboard_id:
            return None
        result = await self._session.execute(
            select(Dashboard).where(Dashboard.tenant_id == tenant_id, Dashboard.id == dashboard_id)
        )
        return result.scalars().first()

    async def _legacy_folder_dashboard_is_active(self, *, viewer_session: SharingViewerSession) -> bool:
        result = await self._session.execute(
            select(SharingGrant, SharingCompatibilityLink)
            .join(SharingCompatibilityLink, SharingCompatibilityLink.grant_id == SharingGrant.id)
            .where(
                SharingGrant.id == viewer_session.grant_id,
                SharingCompatibilityLink.legacy_surface == "folder_dashboard",
            )
        )
        row = result.first()
        if row is None:
            return True
        _, link = row
        try:
            legacy_id = _coerce_uuid(link.legacy_id)
        except ValueError:
            return False
        legacy = await self._session.get(FolderDashboard, legacy_id)
        return legacy is not None and legacy.dashboard_id == viewer_session.object_version_id

    async def _require_active_grant(self, *, tenant_id: UUID, grant_id: UUID) -> SharingGrant:
        result = await self._session.execute(
            select(SharingGrant).where(
                SharingGrant.tenant_id == tenant_id,
                SharingGrant.id == grant_id,
                SharingGrant.status == "active",
                SharingGrant.revoked_at.is_(None),
            )
        )
        grant = result.scalars().first()
        if grant is None:
            raise ValueError("active sharing grant not found")
        return grant

    def _build_secret(self, *, tenant_id: UUID, grant_id: UUID, actor_id: UUID, secret: str) -> SharingSecret:
        salt = secrets.token_urlsafe(32)
        return SharingSecret(
            tenant_id=tenant_id,
            grant_id=grant_id,
            secret_type="password",
            algorithm=_SECRET_ALGORITHM,
            salt=salt,
            verifier_hash=_hash_secret(secret=secret, salt=salt),
            status="active",
            created_by=actor_id,
        )

    async def _replace_password_secret(
        self,
        *,
        tenant_id: UUID,
        grant_id: UUID,
        actor_id: UUID,
        password: str | None,
        has_password: bool,
    ) -> None:
        result = await self._session.execute(
            select(SharingSecret).where(
                SharingSecret.grant_id == grant_id,
                SharingSecret.secret_type == "password",
                SharingSecret.status == "active",
            )
        )
        active_secret = result.scalars().first()
        if active_secret is not None and (password is not None or not has_password):
            active_secret.status = "rotated"
            active_secret.rotated_at = self._now()
        if password:
            self._session.add(
                self._build_secret(
                    tenant_id=tenant_id,
                    grant_id=grant_id,
                    actor_id=actor_id,
                    secret=password,
                )
            )

    def _audit_event(
        self,
        *,
        tenant_id: UUID,
        grant: SharingGrant,
        actor_id: str,
        action: str,
        outcome: str,
        details: dict[str, Any],
        viewer_session_id: UUID | None = None,
    ) -> SharingAuditEvent:
        return SharingAuditEvent(
            tenant_id=tenant_id,
            grant_id=grant.id,
            viewer_session_id=viewer_session_id,
            object_type=grant.object_type,
            object_id=grant.object_id,
            object_version_id=grant.object_version_id,
            actor_type="human",
            actor_id=actor_id,
            action=action,
            outcome=outcome,
            details_json=sanitize_error_payload(details),
        )

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)


def _coerce_uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _hash_secret(*, secret: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), 210000)
    return "sha256:" + digest.hex()


def _digest_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _dashboard_digest(dashboard: Dashboard) -> str:
    return "sha256:" + hashlib.sha256((dashboard.html_content or "").encode()).hexdigest()
