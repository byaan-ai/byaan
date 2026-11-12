from __future__ import annotations

import asyncio
import base64
import grp
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from server.config.storage import handoffs_root
from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

GITHUB_HOST = "https://github.com"
MAX_SYNC_BYTES = 200 * 1024 * 1024
DEFAULT_TIMEOUT = 180


class GitError(Exception):
    pass


@dataclass(frozen=True)
class Workspace:
    root: Path
    repo: Path
    work: Path
    home: Path

    @classmethod
    def for_handoff(cls, handoff_id: UUID) -> Workspace:
        root = handoffs_root() / str(handoff_id)
        return cls(root=root, repo=root / "repo", work=root / "work", home=root / "home")

    @property
    def exists(self) -> bool:
        return (self.repo / ".git").is_dir() and self.work.is_dir()


@dataclass(frozen=True)
class FileChange:
    status: str
    path: str
    added: int
    removed: int


def agent_group_id() -> int | None:
    name = os.getenv("HANDOFF_AGENT_GROUP")
    if not name:
        return None
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError:
        logger.warning(f"[HANDOFF] Agent group {name!r} not found; running without OS isolation")
        return None


def remote_url(repo_full_name: str) -> str:
    return f"{GITHUB_HOST}/{repo_full_name}.git"


def _git_env(token: str | None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    if token:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"http.{GITHUB_HOST}/.extraheader",
                "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            }
        )
    return env


async def run_git(
    args: list[str],
    cwd: Path | None = None,
    *,
    token: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    stdin: bytes | None = None,
) -> str:
    cmd = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd) if cwd else None,
        env=_git_env(token),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin), timeout=timeout)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    if process.returncode != 0:
        message = err.decode(errors="replace").strip()
        raise GitError(f"git {args[0]} failed: {message[-500:]}")
    return out.decode(errors="replace")


def _set_group_shared(path: Path, gid: int | None) -> None:
    if gid is None:
        return
    for current, dirs, files in os.walk(path):
        os.chown(current, -1, gid)
        os.chmod(current, os.stat(current).st_mode | stat.S_IRWXG | stat.S_ISGID)
        for name in files:
            target = os.path.join(current, name)
            if os.path.islink(target):
                continue
            os.chown(target, -1, gid)
            os.chmod(target, os.stat(target).st_mode | stat.S_IRGRP | stat.S_IWGRP)
        dirs[:] = [name for name in dirs if not os.path.islink(os.path.join(current, name))]


async def create_workspace(
    handoff_id: UUID,
    repo_full_name: str,
    branch: str,
    token: str,
    *,
    new_branch: str | None = None,
    depth: int = 50,
) -> Workspace:
    workspace = Workspace.for_handoff(handoff_id)
    await asyncio.to_thread(remove_workspace, workspace.root)
    gid = agent_group_id()

    workspace.root.mkdir(parents=True)
    if gid is not None:
        os.chown(workspace.root, -1, gid)
    os.chmod(workspace.root, 0o2710 if gid is not None else 0o700)

    await run_git(
        ["clone", "--depth", str(depth), "--single-branch", "--branch", branch, remote_url(repo_full_name), "repo"],
        cwd=workspace.root,
        token=token,
        timeout=600,
    )
    os.chmod(workspace.repo, 0o700)
    if new_branch:
        await run_git(["checkout", "-b", new_branch], cwd=workspace.repo)

    await run_git(["clone", "--local", "--no-hardlinks", "--quiet", str(workspace.repo), "work"], cwd=workspace.root)
    await run_git(["remote", "set-url", "origin", remote_url(repo_full_name)], cwd=workspace.work)
    workspace.home.mkdir()
    await asyncio.to_thread(_set_group_shared, workspace.work, gid)
    await asyncio.to_thread(_set_group_shared, workspace.home, gid)
    return workspace


def remove_workspace(root: Path) -> None:
    if not root.exists():
        return
    base = handoffs_root().resolve()
    resolved = root.resolve()
    if base not in resolved.parents:
        raise GitError(f"Refusing to delete {resolved}: outside {base}")

    def _retry_writable(func, path, _exc_info):
        parent = os.path.dirname(path)
        os.chmod(parent, os.stat(parent).st_mode | stat.S_IWUSR | stat.S_IXUSR)
        func(path)

    shutil.rmtree(resolved, onerror=_retry_writable)


async def head_sha(cwd: Path) -> str:
    return (await run_git(["rev-parse", "HEAD"], cwd=cwd)).strip()


async def remote_branch_exists(repo_full_name: str, branch: str, token: str) -> bool:
    out = await run_git(["ls-remote", "--heads", remote_url(repo_full_name), branch], token=token, timeout=60)
    return bool(out.strip())


async def _ignored(repo: Path, paths: list[str]) -> set[str]:
    if not paths:
        return set()
    payload = "\0".join(paths).encode() + b"\0"
    process = await asyncio.create_subprocess_exec(
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "check-ignore",
        "-z",
        "--stdin",
        cwd=str(repo),
        env=_git_env(None),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(payload), timeout=DEFAULT_TIMEOUT)
    if process.returncode not in (0, 1):
        raise GitError(f"git check-ignore failed: {err.decode(errors='replace')[-300:]}")
    return {p for p in out.decode(errors="replace").split("\0") if p}


async def _collect_work_files(work: Path, repo: Path) -> tuple[list[tuple[str, int, str, bool]], list[int]]:
    """Walk Claude's tree pinning every directory component with O_NOFOLLOW, so a concurrent process can't
    swap a directory for a symlink mid-walk and redirect the copy outside the workspace. Returns
    (rel, parent_dir_fd, name, is_symlink) tuples plus the open dir fds; the caller must close them."""
    collected: list[tuple[str, int, str, bool]] = []
    dir_fds: list[int] = [os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)]
    pending: list[tuple[str, int]] = [("", dir_fds[0])]
    total = 0
    while pending:
        rel_dir, dir_fd = pending.pop()
        entries = []
        for name in os.listdir(dir_fd):
            if name == ".git":
                continue
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            rel = f"{rel_dir}/{name}" if rel_dir else name
            entries.append((rel, name, st, stat.S_ISDIR(st.st_mode)))
        ignored = await _ignored(repo, [f"{rel}/" if is_dir else rel for rel, _, _, is_dir in entries])
        for rel, name, st, is_dir in entries:
            if is_dir:
                if f"{rel}/" in ignored or rel in ignored:
                    continue
                try:
                    child_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
                except OSError:
                    continue  # swapped for a symlink or removed since we listed it; skip it
                dir_fds.append(child_fd)
                pending.append((rel, child_fd))
                continue
            if rel in ignored:
                continue
            if stat.S_ISLNK(st.st_mode) or stat.S_ISREG(st.st_mode):
                total += st.st_size
                if total > MAX_SYNC_BYTES:
                    raise GitError("Changes are larger than 200 MB; refusing to sync")
                collected.append((rel, dir_fd, name, stat.S_ISLNK(st.st_mode)))
    return collected, dir_fds


def _mirror_files(repo: Path, collected: list[tuple[str, int, str, bool]]) -> None:
    repo_root = repo.resolve()
    for rel, parent_fd, name, is_symlink in collected:
        target = repo / rel
        if repo_root not in target.parent.resolve().parents and target.parent.resolve() != repo_root:
            raise GitError(f"Refusing to write outside the repository: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if is_symlink:
            os.symlink(os.readlink(name, dir_fd=parent_fd), target)
            continue
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            raise GitError(f"{rel} changed while syncing; refusing to copy it") from exc
        with os.fdopen(fd, "rb") as src, open(target, "xb") as dst:
            if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
                raise GitError(f"{rel} is not a regular file; refusing to copy it")
            shutil.copyfileobj(src, dst)
            os.fchmod(dst.fileno(), 0o755 if os.fstat(src.fileno()).st_mode & stat.S_IXUSR else 0o644)


async def sync_work_into_repo(workspace: Workspace) -> None:
    """Mirror Claude's files into Byaan's clone without ever running git against Claude's .git."""
    collected, dir_fds = await _collect_work_files(workspace.work, workspace.repo)
    try:
        present = {rel for rel, _, _, _ in collected}
        tracked = set((await run_git(["ls-files", "-z"], cwd=workspace.repo)).split("\0")) - {""}
        for rel in tracked - present:
            target = workspace.repo / rel
            if target.is_symlink() or target.exists():
                target.unlink()
        await asyncio.to_thread(_mirror_files, workspace.repo, collected)
    finally:
        for fd in dir_fds:
            os.close(fd)
    await run_git(["add", "-A"], cwd=workspace.repo)


async def staged_changes(repo: Path) -> list[FileChange]:
    status_out = await run_git(["diff", "--cached", "--name-status", "-z", "--no-renames"], cwd=repo)
    numstat_out = await run_git(["diff", "--cached", "--numstat", "-z", "--no-renames"], cwd=repo)

    counts: dict[str, tuple[int, int]] = {}
    for record in numstat_out.split("\0"):
        if not record:
            continue
        added, removed, path = record.split("\t", 2)
        counts[path] = (int(added) if added.isdigit() else 0, int(removed) if removed.isdigit() else 0)

    parts = [p for p in status_out.split("\0") if p]
    changes = []
    for status_code, path in zip(parts[0::2], parts[1::2], strict=False):
        added, removed = counts.get(path, (0, 0))
        changes.append(FileChange(status=status_code[0], path=path, added=added, removed=removed))
    return changes


async def staged_added_lines(repo: Path) -> str:
    return await run_git(
        [
            "-c",
            "core.quotePath=false",
            "diff",
            "--cached",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
        ],
        cwd=repo,
    )


async def commit(repo: Path, message: str, author_name: str, author_email: str) -> str:
    await run_git(
        [
            "-c",
            f"user.name={author_name}",
            "-c",
            f"user.email={author_email}",
            "commit",
            "--no-verify",
            "--no-gpg-sign",
            "-m",
            message,
        ],
        cwd=repo,
    )
    return await head_sha(repo)


async def push(repo: Path, repo_full_name: str, branch: str, token: str) -> None:
    if not branch.startswith("byaan/"):
        raise GitError(f"Refusing to push non-Byaan branch {branch!r}")
    await run_git(
        ["push", "--no-verify", remote_url(repo_full_name), f"HEAD:refs/heads/{branch}"],
        cwd=repo,
        token=token,
        timeout=300,
    )


async def reset_to_remote(repo: Path, repo_full_name: str, branch: str, token: str) -> str:
    await run_git(["fetch", "--depth", "50", remote_url(repo_full_name), branch], cwd=repo, token=token, timeout=300)
    await run_git(["reset", "--hard", "FETCH_HEAD"], cwd=repo)
    return await head_sha(repo)


def detect_repo_features(repo: Path) -> dict[str, bool]:
    attributes = repo / ".gitattributes"
    uses_lfs = attributes.is_file() and "filter=lfs" in attributes.read_text(errors="ignore")
    return {"lfs": uses_lfs, "submodules": (repo / ".gitmodules").is_file()}


def read_repo_instructions(repo: Path, limit: int = 20_000) -> str:
    chunks = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = repo / name
        if path.is_file() and not path.is_symlink():
            chunks.append(f"## {name}\n{path.read_text(errors='ignore')[:limit]}")
    return "\n\n".join(chunks)[:limit]
