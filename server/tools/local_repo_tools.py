from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def _get_local_repo(ctx: RunContextWrapper, repo_id: str) -> dict | None:
    return ctx.context.get("local_repos", {}).get(repo_id)


@function_tool
async def read_local_repo_file(ctx: RunContextWrapper, repo_id: str, path: str) -> str:
    """Read a file's content from a connected local repository.

    Each line starts with its line number and a tab. Cite these numbers as file:line, and leave them out when
    quoting code.

    Args:
        repo_id: The local repository ID
        path: Relative file path within the repository
    """
    from server.services.local_repo_service import get_local_file_content
    from server.tools.github_tools import number_lines

    repo_data = _get_local_repo(ctx, repo_id)
    if not repo_data:
        return json.dumps({"error": f"Local repository {repo_id} not found in context"})

    try:
        content = await get_local_file_content(repo_data["local_path"], path)
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    if content is None:
        return json.dumps({"error": f"File not found, too large, or unreadable: {path}"})

    return json.dumps({"path": path, "content": number_lines(content)})


@function_tool
async def search_local_repo_files(ctx: RunContextWrapper, repo_id: str, query: str) -> str:
    """Search file paths in a connected local repository's file tree (case-insensitive).

    Args:
        repo_id: The local repository ID
        query: Search query to match against file paths
    """
    from server.services.local_repo_service import search_local_files

    repo_data = _get_local_repo(ctx, repo_id)
    if not repo_data:
        return json.dumps({"error": f"Local repository {repo_id} not found in context"})

    file_tree = repo_data.get("file_tree", [])
    matches = search_local_files(file_tree, query)
    return json.dumps({"query": query, "matches": matches, "total": len(matches)})


@function_tool
async def grep_local_repo(ctx: RunContextWrapper, repo_id: str, pattern: str, file_extensions: str = "") -> str:
    """Search file contents in a connected local repository using regex pattern.

    Args:
        repo_id: The local repository ID
        pattern: Regex pattern to search for in file contents
        file_extensions: Comma-separated file extensions to filter (e.g. ".py,.ts"). Empty for all files.
    """
    from server.services.local_repo_service import grep_local_files

    repo_data = _get_local_repo(ctx, repo_id)
    if not repo_data:
        return json.dumps({"error": f"Local repository {repo_id} not found in context"})

    ext_list = [e.strip() for e in file_extensions.split(",") if e.strip()] if file_extensions else None
    try:
        results = await grep_local_files(repo_data["local_path"], pattern, ext_list)
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"pattern": pattern, "matches": results, "total": len(results)})


@function_tool
async def list_local_repo_directory(ctx: RunContextWrapper, repo_id: str, path: str = "") -> str:
    """List files and directories in a specific path within a connected local repository.

    Args:
        repo_id: The local repository ID
        path: Relative directory path (empty string for repository root)
    """
    from server.services.local_repo_service import list_local_directory

    repo_data = _get_local_repo(ctx, repo_id)
    if not repo_data:
        return json.dumps({"error": f"Local repository {repo_id} not found in context"})

    try:
        entries = await list_local_directory(repo_data["local_path"], path)
    except PermissionError as e:
        return json.dumps({"error": str(e)})
    return json.dumps({"path": path or "/", "entries": entries, "total": len(entries)})


@function_tool
async def get_local_repo_skill(ctx: RunContextWrapper, repo_id: str, skill_type: str) -> str:
    """Get a pre-analyzed skill (codebase, data_layer, or custom) for a connected local repository.

    Args:
        repo_id: The local repository ID
        skill_type: The skill type to retrieve (codebase, data_layer, or a custom skill name)
    """
    repo_data = _get_local_repo(ctx, repo_id)
    if not repo_data:
        return json.dumps({"error": f"Local repository {repo_id} not found in context"})

    skills = repo_data.get("skills", {})
    skill = skills.get(skill_type)
    if not skill:
        available = list(skills.keys())
        return json.dumps({"error": f"Skill '{skill_type}' not found. Available: {available}"})

    return json.dumps(
        {"skill_type": skill_type, "skill_name": skill.get("name", ""), "content": skill.get("content", "")}
    )


def get_local_repo_tools() -> list:
    return [
        read_local_repo_file,
        search_local_repo_files,
        grep_local_repo,
        list_local_repo_directory,
        get_local_repo_skill,
    ]
