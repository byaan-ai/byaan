from __future__ import annotations

import secrets
import string
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from server.config import storage
from server.schemas.handoff import HandoffPaths
from server.services import handoff_git
from server.services.handoff_checks import disallowed_paths, path_matches, scan_added_lines
from server.services.handoff_github import summarize_checks

pytestmark = pytest.mark.handoff


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFFS_STORAGE_DIR", str(tmp_path / "handoffs"))
    monkeypatch.delenv("HANDOFF_AGENT_GROUP", raising=False)
    storage.handoffs_root.cache_clear()
    hosts = tmp_path / "remote"
    bare = hosts / "acme" / "api.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "app").mkdir()
    (seed / "app" / "scheduler.py").write_text("def send():\n    return None\n")
    (seed / "README.md").write_text("readme\n")
    (seed / ".gitignore").write_text("node_modules/\n*.pyc\n")
    _git(seed, "add", "-A")
    _git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    _git(seed, "push", str(bare), "main")

    monkeypatch.setattr(handoff_git, "GITHUB_HOST", f"file://{hosts}")
    yield bare
    storage.handoffs_root.cache_clear()


async def test_workspace_sync_commit_push(remote):
    workspace = await handoff_git.create_workspace(uuid4(), "acme/api", "main", "tok", new_branch="byaan/h-1-fix")
    work = workspace.work

    (work / "app" / "scheduler.py").write_text("def send():\n    return 'clinic'\n")
    (work / "app" / "new.py").write_text("x = 1\n")
    (work / "README.md").unlink()
    (work / "node_modules").mkdir()
    (work / "node_modules" / "big.js").write_text("ignored")
    _git(work, "-c", "user.name=c", "-c", "user.email=c@c", "commit", "-am", "claude committed itself")
    (work / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\ntouch /tmp/pwned-handoff\nexit 1\n")
    (work / ".git" / "hooks" / "pre-commit").chmod(0o755)

    await handoff_git.sync_work_into_repo(workspace)
    changes = {c.path: c.status for c in await handoff_git.staged_changes(workspace.repo)}
    assert changes == {"app/scheduler.py": "M", "app/new.py": "A", "README.md": "D"}

    sha = await handoff_git.commit(workspace.repo, "fix", "Byaan Bot", "bot@byaan.test")
    await handoff_git.push(workspace.repo, "acme/api", "byaan/h-1-fix", "tok")
    assert _git(remote, "rev-parse", "byaan/h-1-fix").strip() == sha
    assert not Path("/tmp/pwned-handoff").exists()
    assert await handoff_git.remote_branch_exists("acme/api", "byaan/h-1-fix", "tok")


async def test_push_refuses_non_byaan_branch(remote):
    workspace = await handoff_git.create_workspace(uuid4(), "acme/api", "main", "tok")
    with pytest.raises(handoff_git.GitError):
        await handoff_git.push(workspace.repo, "acme/api", "main", "tok")


async def test_remove_workspace_refuses_outside_root(tmp_path, remote):
    with pytest.raises(handoff_git.GitError):
        handoff_git.remove_workspace(tmp_path)


def test_path_policy():
    paths = HandoffPaths(allow=["app/**", "tests/**"], deny=["app/migrations/**"])
    assert path_matches("app/x/y.py", "app/**")
    assert path_matches("deep/dir/file.md", "*.md")
    assert disallowed_paths(["app/a.py", "app/migrations/0001.py", ".github/workflows/ci.yml", "setup.py"], paths) == [
        "app/migrations/0001.py",
        ".github/workflows/ci.yml",
        "setup.py",
    ]
    assert disallowed_paths([".github/workflows/ci.yml"], HandoffPaths()) == [".github/workflows/ci.yml"]


def _fake_aws_key() -> str:
    return "AKIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))


def test_secret_scan_reports_path_and_line():
    diff = (
        "diff --git a/app/c.py b/app/c.py\n"
        "+++ b/app/c.py\n"
        "@@ -0,0 +3,2 @@\n"
        "+x = compute(total)\n"
        f'+aws = "{_fake_aws_key()}"\n'
    )
    findings = scan_added_lines(diff)
    assert [(f.path, f.line, f.kind) for f in findings] == [("app/c.py", 4, "AWS Access Key")]


def test_summarize_checks_states():
    assert summarize_checks([], {})["state"] == "none"
    running = [{"name": "test", "status": "in_progress"}]
    assert summarize_checks(running, {})["state"] == "pending"
    failed = [
        {"name": "lint", "status": "completed", "conclusion": "failure", "id": 7, "app": {"slug": "github-actions"}}
    ]
    summary = summarize_checks(failed + [{"name": "t", "status": "completed", "conclusion": "success"}], {})
    assert summary["state"] == "failure"
    assert summary["failing"][0]["is_actions"]
    status = {"statuses": [{"context": "ci/circle", "state": "success"}]}
    assert summarize_checks([], status)["state"] == "success"


def test_local_cli_wrapper_scrubs_environment(tmp_path, monkeypatch):
    import os
    import subprocess as sp

    from server.services.handoff_agent import _scrubbed_cli

    monkeypatch.setenv("HANDOFFS_STORAGE_DIR", str(tmp_path / "handoffs"))
    storage.handoffs_root.cache_clear()
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nenv | cut -d= -f1 | sort\necho ARGS=$*\n")
    fake.chmod(0o755)

    wrapper = _scrubbed_cli(str(fake))
    env = {**os.environ, "DATABASE_URL": "postgres://secret", "APP_SECRET": "s", "CLAUDE_CODE_OAUTH_TOKEN": "tok"}
    output = sp.run([wrapper, "-p", "hi"], env=env, capture_output=True, text=True, check=True).stdout

    names = set(output.split())
    assert "CLAUDE_CODE_OAUTH_TOKEN" in names and "PATH" in names
    assert "DATABASE_URL" not in names and "APP_SECRET" not in names
    assert "ARGS=-p hi" in output
    storage.handoffs_root.cache_clear()


def test_failing_step_output_keeps_the_whole_failing_step():
    from server.services.handoff_github import failing_step_output

    log = (
        "2026-11-04T20:48:20.1Z ##[group]Run pip install -r requirements.txt\n"
        "2026-11-04T20:48:21.1Z installed\n"
        "2026-11-04T20:48:22.1Z ##[group]Run ruff check .\n"
        "2026-11-04T20:48:23.1Z BLE001 Do not catch blind exception\n"
        + "".join(f"2026-11-04T20:48:24.1Z line {i}\n" for i in range(400))
        + "2026-11-04T20:48:26.1Z RUF010 Use explicit conversion flag\n"
        "2026-11-04T20:48:27.1Z ##[error]Process completed with exit code 1.\n"
        "2026-11-04T20:48:28.1Z ##[group]Run cleanup\n"
    )
    output = failing_step_output(log, 50_000)
    assert output.startswith("##[group]Run ruff check .")
    assert "BLE001" in output and "RUF010" in output
    assert "pip install" not in output and "2026-11-04" not in output


@pytest.mark.parametrize(
    ("line", "blocked"),
    [
        ('os.environ["OPENAI_API_KEY"] = "dummy"', False),
        ('os.environ["TWILIO_AUTH_TOKEN"] = "test-token"', False),
        ('password = "changeme"', False),
        ('api_key = "<your-api-key>"', False),
        (f'api_key = "sk-live-{secrets.token_hex(12)}"', True),
        (f'aws = "{_fake_aws_key()}"', True),
        (f'token = "ghp_{secrets.token_hex(18)}"', True),
    ],
)
def test_secret_scan_ignores_placeholders_but_blocks_real_secrets(line, blocked):
    diff = f"+++ b/tests/test_x.py\n@@ -0,0 +1,1 @@\n+{line}\n"
    assert bool(scan_added_lines(diff)) is blocked
