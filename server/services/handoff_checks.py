from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import lru_cache

from server.schemas.handoff import HandoffPaths

_EXCLUDED_PLUGINS = {"Base64HighEntropyString", "HexHighEntropyString", "IPPublicDetector"}
_KEYWORD_TYPE = "Secret Keyword"
_PLACEHOLDER = re.compile(
    r"^(dummy|test|testing|fake|mock|example|sample|placeholder|changeme|change[-_]?me|secret|password|"
    r"token|api[-_]?key|none|null|todo|redacted|your[-_].*|<.*>|\$\{.*\}|x+|\*+|\.+)([-_]?\w{0,8})?$",
    re.IGNORECASE,
)


def _looks_like_real_value(value: str) -> bool:
    """Generic keyword hits need a value that looks like an actual credential, not a placeholder."""
    value = value.strip()
    if len(value) < 12 or _PLACEHOLDER.match(value):
        return False
    counts = {ch: value.count(ch) for ch in set(value)}
    entropy = -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())
    return entropy >= 3.0


def _diff_path(header: str) -> str:
    """Path from a `+++` header; git C-quotes names with special characters even with core.quotePath off."""
    if header.startswith('"') and header.endswith('"'):
        header = header[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape").encode("latin-1").decode()
    return header[2:] if header.startswith("b/") else ""


@dataclass(frozen=True)
class SecretFinding:
    path: str
    line: int
    kind: str


@lru_cache(maxsize=512)
def _glob_regex(pattern: str) -> re.Pattern[str]:
    parts = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            parts.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            parts.append(".*")
            i += 2
        elif pattern[i] == "*":
            parts.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            parts.append("[^/]")
            i += 1
        else:
            parts.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(parts) + r"\Z")


def path_matches(path: str, pattern: str) -> bool:
    path = path.lstrip("/")
    if "/" not in pattern.rstrip("/"):
        return bool(_glob_regex(pattern).match(path.rsplit("/", 1)[-1])) or bool(_glob_regex(pattern).match(path))
    if pattern.endswith("/"):
        pattern = f"{pattern}**"
    return bool(_glob_regex(pattern).match(path))


def path_allowed(path: str, paths: HandoffPaths) -> bool:
    if any(path_matches(path, pattern) for pattern in paths.effective_deny()):
        return False
    return not paths.allow or any(path_matches(path, pattern) for pattern in paths.allow)


def disallowed_paths(changed: list[str], paths: HandoffPaths) -> list[str]:
    return [path for path in changed if not path_allowed(path, paths)]


@lru_cache(maxsize=1)
def _plugins() -> list[dict[str, str]]:
    from detect_secrets.core.plugins.util import get_mapping_from_secret_type_to_class

    return [
        {"name": cls.__name__}
        for cls in get_mapping_from_secret_type_to_class().values()
        if cls.__name__ not in _EXCLUDED_PLUGINS
    ]


def scan_added_lines(unified_diff: str) -> list[SecretFinding]:
    from detect_secrets.core.scan import scan_line
    from detect_secrets.settings import transient_settings

    findings: list[SecretFinding] = []
    current_path = ""
    line_no = 0
    with transient_settings({"plugins_used": _plugins()}):
        for raw in unified_diff.splitlines():
            if raw.startswith("+++ "):
                current_path = _diff_path(raw[4:])
                continue
            if raw.startswith("@@"):
                match = re.search(r"\+(\d+)", raw)
                line_no = int(match.group(1)) if match else 0
                continue
            if raw.startswith("+") and current_path:
                for secret in scan_line(raw[1:]):
                    if secret.type == _KEYWORD_TYPE and not _looks_like_real_value(secret.secret_value or ""):
                        continue
                    findings.append(SecretFinding(path=current_path, line=line_no, kind=secret.type))
                line_no += 1
    return findings
