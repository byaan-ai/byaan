"""
Utilities for sanitizing error and evidence payloads before returning them to clients.
"""

from __future__ import annotations

import re
from typing import Any

REDACTION_TEXT = "[REDACTED]"

SENSITIVE_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "api-key",
    "access_token",
    "access-token",
    "refresh_token",
    "refresh-token",
    "token",
    "raw_token",
    "raw-token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "verifier",
    "credential",
    "credentials",
    "connection_string",
    "connection-string",
    "sql",
    "query",
}

SENSITIVE_KEY_PATTERN = (
    r"authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|raw[_-]?token|"
    r"token|password|passwd|pwd|secret|verifier|credentials?|connection[_-]?string|sql|query"
)

SENSITIVE_PATTERNS = [
    (re.compile(r"\bselect\b[\s\S]*?\bfrom\b[\s\S]*", re.IGNORECASE), "[REDACTED_SQL]"),
    (re.compile(r"(Authorization:\s*)(Bearer\s+)?[\w\-\.]+", re.IGNORECASE), r"\1[REDACTED]"),
    (
        re.compile(rf"((?:\"|')?(?:{SENSITIVE_KEY_PATTERN})(?:\"|')?\s*[:=]\s*)([\"'])(.*?)(\2)", re.IGNORECASE),
        rf"\1\2{REDACTION_TEXT}\4",
    ),
    (
        re.compile(rf"((?:\"|')?(?:{SENSITIVE_KEY_PATTERN})(?:\"|')?\s*[:=]\s*)[^\s,}}\]\n]+", re.IGNORECASE),
        rf"\1{REDACTION_TEXT}",
    ),
    (re.compile(r"Bearer\s+[\w\-\.]+", re.IGNORECASE), "Bearer [REDACTED]"),
    (re.compile(r"mongodb://[^@]+@", re.IGNORECASE), "mongodb://[REDACTED]@"),
    (re.compile(r"postgres://[^@]+@", re.IGNORECASE), "postgres://[REDACTED]@"),
    (re.compile(r"mysql://[^@]+@", re.IGNORECASE), "mysql://[REDACTED]@"),
]


def sanitize_text(message: str) -> str:
    sanitized = message
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def sanitize_error_message(error: Exception | str) -> str:
    return sanitize_text(str(error))


def sanitize_error_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        sanitized: dict[Any, Any] = {}
        for key, value in payload.items():
            key_text = str(key).lower()
            if key_text in SENSITIVE_KEYS or any(sensitive in key_text for sensitive in SENSITIVE_KEYS):
                sanitized[key] = REDACTION_TEXT
            else:
                sanitized[key] = sanitize_error_payload(value)
        return sanitized
    if isinstance(payload, list):
        return [sanitize_error_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return tuple(sanitize_error_payload(item) for item in payload)
    if isinstance(payload, str):
        return sanitize_text(payload)
    return payload
