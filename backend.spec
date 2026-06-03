# -*- mode: python ; coding: utf-8 -*-

# PyInstaller spec file for Byaan backend
# This bundles the FastAPI backend with all dependencies into a single executable
#
# Key features:
# 1. Data files (migrations, prompts, alembic.ini) - required at runtime
# 2. Custom hooks for packages without built-in PyInstaller support
# 3. Hidden imports for dynamically loaded database drivers
#
# IMPORTANT: When adding new data files or runtime dependencies:
# - Add them to the 'datas' list below
# - Test the frozen executable to ensure files are accessible
# - Check server/utils/migrations.py for path resolution logic

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

datas_with_metadata = [
    # Application-specific data files
    # Format: (source_path, destination_path_in_bundle)
    ('server/alembic.ini', 'server/'),        # Alembic configuration for migrations
    ('server/migrations', 'server/migrations'), # Database migration scripts
    ('server/prompts', 'server/prompts'),      # AI prompt templates
    ('server/skills', 'server/skills'),        # Skill definitions (SKILL.md files)
    ('server/example_data', 'example_data'),   # Demo notebooks and sample data
    ('client/src-tauri/resources/config.json', '.'), # Runtime configuration (PostHog, etc.)
]

# Add package metadata for packages that use importlib.metadata at runtime
datas_with_metadata += copy_metadata('duckdb')
datas_with_metadata += copy_metadata('fastmcp')

# litellm ships JSON pricing/model data alongside its Python files; PyInstaller
# misses these because they're loaded via pathlib at runtime, not imports.
datas_with_metadata += collect_data_files('litellm')

a = Analysis(
    ['server/main.py'],
    pathex=[],
    binaries=[],
    datas=datas_with_metadata,
    hiddenimports=[
        # SQLAlchemy dialect drivers (loaded dynamically based on connection string)
        'aiosqlite',  # For sqlite+aiosqlite://
        'asyncpg',    # For postgresql+asyncpg://
        'duckdb',     # DuckDB database engine
        # Pydantic dependencies for email validation
        'email_validator',  # For EmailStr validation in schemas
        # Note: These are needed because they're loaded via string-based driver names
        # in connection URLs, so PyInstaller can't detect them from imports
    ],
    hookspath=['pyinstaller_hooks'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude development and testing packages
        'pytest',
        'unittest',
        'test',
        'tests',
        '_pytest',
        'black',
        'mypy',
        'pylint',
        'flake8',
        'setuptools',
        'pip',
        'wheel',
        'pkg_resources',  # Causes jaraco.text dependency issues
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# Reclassify DuckDB extension bundles as data files so PyInstaller does not try to
# re-sign or strip them (these `.duckdb_extension` binaries already ship signed
# and lack the metadata that macOS strip expects).
duckdb_extension_blobs = []
filtered_binaries = []
for dest_name, src_name, typecode in a.binaries:
    if dest_name.endswith(".duckdb_extension"):
        duckdb_extension_blobs.append((dest_name, src_name))
    else:
        filtered_binaries.append((dest_name, src_name, typecode))

a.binaries = filtered_binaries
a.datas += [(dest_name, src_name, "DATA") for dest_name, src_name in duckdb_extension_blobs]

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [], # No binaries/datas here
    exclude_binaries=True,
    name='backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # Disabled: strip invalidates code signatures on macOS
    upx=False,
    console=True,
)

# Collect everything into a directory
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,  # Disabled: strip invalidates code signatures on macOS
    upx=False,
    name='backend',
)
