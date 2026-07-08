from __future__ import annotations

import json
from uuid import UUID

from agents import RunContextWrapper, function_tool

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


@function_tool
async def get_repo_skill(ctx: RunContextWrapper, repo_id: str, skill_type: str) -> str:
    """Get a specific skill (codebase, data_layer, logging, code_review, or custom) for a connected GitHub repository.

    Args:
        repo_id: The repository ID
        skill_type: The skill type to retrieve (codebase, data_layer, logging, code_review, or a custom skill name)
    """
    github_repos = ctx.context.get("github_repos", {})
    repo_data = github_repos.get(repo_id)
    if not repo_data:
        return json.dumps({"error": f"Repository {repo_id} not found in context"})

    skills = repo_data.get("skills", {})
    skill = skills.get(skill_type)
    if not skill:
        available = list(skills.keys())
        return json.dumps({"error": f"Skill '{skill_type}' not found. Available: {available}"})

    return json.dumps(
        {"skill_type": skill_type, "skill_name": skill.get("name", ""), "content": skill.get("content", "")}
    )


@function_tool
async def list_repo_skills(ctx: RunContextWrapper, repo_id: str) -> str:
    """List all available skills for a connected GitHub repository.

    Args:
        repo_id: The repository ID
    """
    github_repos = ctx.context.get("github_repos", {})
    repo_data = github_repos.get(repo_id)
    if not repo_data:
        return json.dumps({"error": f"Repository {repo_id} not found in context"})

    skills = repo_data.get("skills", {})
    result = [
        {"skill_type": k, "skill_name": v.get("name", ""), "version": v.get("version", 1)} for k, v in skills.items()
    ]
    return json.dumps({"repo": repo_data.get("repo_full_name", ""), "skills": result})


@function_tool
async def search_repo_code(ctx: RunContextWrapper, repo_id: str, query: str) -> str:
    """Search file paths in a connected GitHub repository's file tree.

    Args:
        repo_id: The repository ID
        query: Search query to match against file paths (case-insensitive)
    """
    github_repos = ctx.context.get("github_repos", {})
    repo_data = github_repos.get(repo_id)
    if not repo_data:
        return json.dumps({"error": f"Repository {repo_id} not found in context"})

    file_tree = repo_data.get("file_tree", [])
    query_lower = query.lower()
    matches = [path for path in file_tree if query_lower in path.lower()][:50]
    return json.dumps(
        {"repo": repo_data.get("repo_full_name", ""), "query": query, "matches": matches, "total": len(matches)}
    )


@function_tool
async def get_repo_file(ctx: RunContextWrapper, repo_id: str, path: str) -> str:
    """Fetch a file's content from a connected GitHub repository via the GitHub API.

    Args:
        repo_id: The repository ID
        path: The file path within the repository
    """
    from server.services import github_service

    github_repos = ctx.context.get("github_repos", {})
    token = ctx.context.get("github_token")
    repo_data = github_repos.get(repo_id)

    if not repo_data or not token:
        return json.dumps({"error": "Repository or GitHub token not found in context"})

    repo_full_name = repo_data.get("repo_full_name", "")
    if "/" not in repo_full_name:
        return json.dumps({"error": f"Invalid repo name: {repo_full_name}"})

    owner, repo_name = repo_full_name.split("/", 1)
    content = await github_service.get_file_content(token, owner, repo_name, path)
    if content is None:
        return json.dumps({"error": f"File not found or too large: {path}"})

    return json.dumps({"path": path, "content": content})


@function_tool
async def create_repo_skill(
    ctx: RunContextWrapper, repo_id: str, skill_name: str, description: str, instructions: str
) -> str:
    """Save a custom analysis skill attached to a connected GitHub repository.

    This tool does NOT call an LLM. You — the agent — must compose the full markdown analysis
    yourself first by reading the repo via `get_repo_file` / `search_repo_code` / `get_repo_skill`,
    then pass the finished markdown as `instructions`. The tool just persists it and returns.

    Args:
        repo_id: The repository ID (must be a connected GitHub repo in context)
        skill_name: Short name for the skill (e.g., "Security Audit", "API Documentation")
        description: One-line summary of what the skill covers (max 500 chars)
        instructions: The full markdown analysis the agent has already written. Required, non-empty.
    """
    from server.db.session import AsyncSessionFactory
    from server.repositories.custom_skill import CustomSkillRepository

    if not instructions or not instructions.strip():
        return json.dumps({"error": "instructions must be a non-empty markdown analysis written by the agent"})

    github_repos = ctx.context.get("github_repos", {})
    repo_data = github_repos.get(repo_id)
    if not repo_data:
        return json.dumps({"error": f"Repository {repo_id} not found in context"})

    tenant_id = ctx.context.get("tenant_id")
    user_id = ctx.context.get("user_id")
    if not all([tenant_id, user_id]):
        return json.dumps({"error": "Missing required context (tenant_id or user_id)"})

    try:
        async with AsyncSessionFactory() as session:
            skill_repo = CustomSkillRepository(session)
            skill = await skill_repo.upsert_github_skill(
                tenant_id=tenant_id,
                created_by=user_id,
                github_repo_id=UUID(repo_id),
                github_analysis_type=f"custom:{skill_name}",
                name=skill_name,
                description=description[:500],
                instructions=instructions,
            )

        skill_key = f"custom:{skill_name}"
        repo_data.setdefault("skills", {})[skill_key] = {
            "name": skill_name,
            "content": skill.instructions,
        }

        return json.dumps(
            {
                "status": "created",
                "skill_name": skill_name,
                "skill_key": skill_key,
                "content": skill.instructions,
            }
        )
    except Exception as e:
        logger.error(f"[CREATE REPO SKILL] Failed: {e}", exc_info=True)
        return json.dumps({"error": f"Failed to save skill: {str(e)}"})


def get_github_tools() -> list:
    return [get_repo_skill, list_repo_skills, search_repo_code, get_repo_file, create_repo_skill]
