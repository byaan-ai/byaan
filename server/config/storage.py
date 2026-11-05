"""
Helpers for resolving dataset and DuckDB storage directories.

All paths default to locations under ``server/.data`` so deployments remain
self-contained. Environments can override these defaults with environment
variables when they need to place data on larger or shared volumes.
"""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]


def get_persistent_data_path() -> Path:
    """Return the OS-specific persistent user data directory."""
    app_name = "com.byaan.desktop"
    system = platform.system()

    if system == "Windows":
        return Path(os.path.expandvars("%APPDATA%")) / app_name
    elif system == "Darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    else:
        return Path.home() / ".local" / "share" / app_name


if getattr(sys, "frozen", False) and platform.system() in ["Darwin", "Windows", "Linux"]:
    DEFAULT_DATA_DIR = get_persistent_data_path()
else:
    DEFAULT_DATA_DIR = BASE_DIR / ".data"

# Ensure the persistent directory is created if it doesn't exist
DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_DATASETS_DIR = DEFAULT_DATA_DIR / "datasets"


def _resolve_path(value: str | None, fallback: Path) -> Path:
    """Expand variables/user home and normalize to an absolute path."""
    if value:
        return Path(os.path.expandvars(os.path.expanduser(value))).resolve()
    return fallback.resolve()


@lru_cache(maxsize=1)
def datasets_root() -> Path:
    """
    Return the root directory for uploaded datasets, creating it if needed.

    Environment variable ``DATASETS_STORAGE_DIR`` can override the default
    location (``server/.data/datasets``).
    """
    path = _resolve_path(os.getenv("DATASETS_STORAGE_DIR"), DEFAULT_DATASETS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def duckdb_root() -> Path:
    """
    Return the directory that should hold DuckDB catalog files.

    Defaults to ``<datasets_root>/duckdb`` but can be overridden with the
    ``DUCKDB_STORAGE_DIR`` environment variable.
    """
    default = datasets_root() / "duckdb"
    path = _resolve_path(os.getenv("DUCKDB_STORAGE_DIR"), default)
    path.mkdir(parents=True, exist_ok=True)
    return path


def dataset_directory(dataset_id: str) -> Path:
    """
    Return the directory dedicated to a dataset.

    The directory is namespaced by dataset id under the datasets root and
    includes subdirectories for raw uploads and DuckDB exports.
    """
    base = datasets_root() / str(dataset_id)
    (base / "raw").mkdir(parents=True, exist_ok=True)
    (base / "duckdb").mkdir(parents=True, exist_ok=True)
    return base


@lru_cache(maxsize=1)
def handoffs_root() -> Path:
    """Per-handoff workspaces; outside the frozen app they live in the temp dir so dev hot-reload never sees the clones."""
    default = (
        DEFAULT_DATA_DIR / "handoffs"
        if getattr(sys, "frozen", False)
        else Path(tempfile.gettempdir()) / "byaan-handoffs"
    )
    path = _resolve_path(os.getenv("HANDOFFS_STORAGE_DIR"), default)
    path.mkdir(parents=True, exist_ok=True)
    return path
