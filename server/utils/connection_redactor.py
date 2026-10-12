"""
Utility for redacting sensitive fields from connection objects before sending to frontend.

This module provides a centralized way to ensure database credentials (passwords,
API keys, connection strings) are never exposed to the frontend.
"""

SENSITIVE_FIELDS = {"password", "api_key", "secret", "token", "connection_string", "username", "user"}

SAFE_FIELDS = {"host", "port", "database", "dataset_type", "db_type", "files", "ssl", "ssl_mode", "schema", "driver"}


def redact_connection_obj(connection_obj: dict | None) -> dict:
    """
    Return only safe fields from connection object for frontend display.

    This function filters out sensitive credentials and only returns display-safe
    information like host, port, database name, etc.

    Args:
        connection_obj: The decrypted connection object (may contain credentials)

    Returns:
        A new dict containing only safe fields for frontend display
    """
    if not connection_obj:
        return {}

    safe_obj = {}
    for key, value in connection_obj.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in SENSITIVE_FIELDS):
            continue
        if key_lower in SAFE_FIELDS or key == "files":
            safe_obj[key] = value

    return safe_obj
