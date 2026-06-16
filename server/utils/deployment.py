"""
Deployment mode detection and feature flags.

This module provides a centralized way to detect which deployment mode
the application is running in, and which features are available.

Deployment Modes (APP_MODE):
- desktop: Desktop DMG app (SQLite, local auth, credit sync)
- community: Local Docker (SQLite, no auth, auto-setup)
- self-hosted: Customer deployment (PostgreSQL, teams, invite-only)
"""

from typing import TYPE_CHECKING

from server.utils.config_loader import is_self_hosted

if TYPE_CHECKING:
    from fastapi import Request


def get_feature_flags() -> dict[str, bool]:
    from server.utils.config_loader import get_google_oauth_config, get_waitlist_config, should_hide_email_auth

    self_hosted = is_self_hosted()
    google_configured = bool(get_google_oauth_config().get("client_id"))
    worker_configured = bool(get_waitlist_config().get("worker_url"))

    email_auth_enabled = (not self_hosted) or (not google_configured)
    if should_hide_email_auth():
        email_auth_enabled = False

    return {
        "worker_features_enabled": worker_configured,
        "external_sharing_enabled": (not self_hosted) and worker_configured,
        "notebook_import_enabled": False,
        "public_registration_enabled": False,
        "local_auth_enabled": email_auth_enabled,
        "invitation_only": self_hosted,
        "google_oauth_enabled": self_hosted and google_configured,
        "team_sharing_enabled": self_hosted,
        "enterprise_licensed": self_hosted,
    }


def is_feature_enabled(feature: str) -> bool:
    """
    Check if a specific feature is enabled.

    Args:
        feature: Feature name (e.g., "external_sharing_enabled")

    Returns:
        True if feature is enabled
    """
    return get_feature_flags().get(feature, False)


def get_security_flags() -> dict[str, bool]:
    """
    Get security feature flags based on deployment mode.

    Returns:
        Dict of security feature name to enabled status
    """
    self_hosted = is_self_hosted()

    return {
        # Phase 1 - Critical
        "proxy_headers_enabled": self_hosted,
        "secure_cookies_forced": self_hosted,
        "error_sanitization_enabled": True,  # Always on
        # Phase 2 - High
        "strict_iframe_sandbox": self_hosted,
        # Phase 3 - Medium
        "duckdb_external_access_blocked": True,  # Always on
        "ssrf_protection_enabled": True,  # Always on
        "ssl_verification_enabled": self_hosted,  # Skip for desktop dev convenience
        "log_redaction_enabled": True,  # Always on
    }


def should_use_secure_cookie(request: "Request | None") -> bool:
    """
    Determine if cookies should use the secure flag.

    Checks deployment security flags and request headers to handle
    reverse proxy scenarios (x-forwarded-proto).

    Args:
        request: The FastAPI request object, or None

    Returns:
        True if the secure flag should be set on cookies
    """
    if request is None:
        return False

    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    is_https = forwarded_proto == "https" or request.url.scheme == "https"

    if not is_https:
        return False

    flags = get_security_flags()
    return flags["secure_cookies_forced"] or is_https
