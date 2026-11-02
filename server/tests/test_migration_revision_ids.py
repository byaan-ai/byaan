"""Alembic stores revision ids in alembic_version.version_num, a VARCHAR(32) on PostgreSQL."""

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"
REVISION_PATTERN = re.compile(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", re.M)


def test_revision_ids_fit_postgres_version_column():
    too_long = {}
    for path in MIGRATIONS_DIR.glob("*.py"):
        match = REVISION_PATTERN.search(path.read_text())
        if match and len(match.group(1)) > 32:
            too_long[path.name] = match.group(1)
    assert not too_long, f"Revision ids longer than 32 chars break PostgreSQL migrations: {too_long}"
