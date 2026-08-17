from __future__ import annotations

from datetime import datetime
from typing import Any

from server.models.sharing import SharingAuditEvent, SharingCompatibilityLink, SharingGrant
from server.utils.error_sanitizer import sanitize_error_payload


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def sharing_grant_payload(grant: SharingGrant) -> dict[str, Any]:
    return {
        "id": str(grant.id),
        "tenant_id": str(grant.tenant_id),
        "object_type": grant.object_type,
        "object_id": str(grant.object_id),
        "object_version_id": str(grant.object_version_id) if grant.object_version_id else None,
        "object_version_digest": grant.object_version_digest,
        "mode": grant.mode,
        "channel": grant.channel,
        "audience": grant.audience,
        "status": grant.status,
        "created_by": str(grant.created_by) if grant.created_by else None,
        "expires_at": _dt(grant.expires_at),
        "revoked_at": _dt(grant.revoked_at),
        "revoked_by": str(grant.revoked_by) if grant.revoked_by else None,
        "revocation_reason": grant.revocation_reason,
        "metadata": sanitize_error_payload(grant.metadata_json or {}),
        "created_at": _dt(grant.created_at),
        "updated_at": _dt(grant.updated_at),
    }


def sharing_compatibility_link_payload(link: SharingCompatibilityLink) -> dict[str, Any]:
    return {
        "id": str(link.id),
        "grant_id": str(link.grant_id),
        "legacy_surface": link.legacy_surface,
        "legacy_id": link.legacy_id,
        "metadata": sanitize_error_payload(link.metadata_json or {}),
        "created_at": _dt(link.created_at),
    }


def sharing_audit_event_payload(event: SharingAuditEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "grant_id": str(event.grant_id) if event.grant_id else None,
        "viewer_session_id": str(event.viewer_session_id) if event.viewer_session_id else None,
        "object_type": event.object_type,
        "object_id": str(event.object_id) if event.object_id else None,
        "object_version_id": str(event.object_version_id) if event.object_version_id else None,
        "actor_type": event.actor_type,
        "actor_id": event.actor_id,
        "action": event.action,
        "outcome": event.outcome,
        "details": sanitize_error_payload(event.details_json or {}),
        "created_at": _dt(event.created_at),
    }


def sharing_grant_evidence_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    secret_counts = list(evidence.get("secret_counts") or [])
    has_secret = any(item.get("status") == "active" and int(item.get("count") or 0) > 0 for item in secret_counts)
    return {
        "grant": sharing_grant_payload(evidence["grant"]),
        "compatibility_links": [
            sharing_compatibility_link_payload(link) for link in evidence.get("compatibility_links") or []
        ],
        "has_secret": has_secret,
        "secret_counts": secret_counts,
        "active_viewer_session_count": int(evidence.get("active_viewer_session_count") or 0),
        "audit_events": [sharing_audit_event_payload(event) for event in evidence.get("audit_events") or []],
    }
