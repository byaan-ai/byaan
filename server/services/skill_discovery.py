"""Skill Discovery - Auto-discover skills from SKILL.md files."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def _get_skills_dir() -> Path:
    """Get skills directory path based on deployment mode."""
    from server.utils.config_loader import is_self_hosted

    if is_self_hosted():
        return Path(__file__).parent.parent / "skills"

    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        return Path(sys._MEIPASS) / "server" / "skills"  # type: ignore[attr-defined]
    return Path(__file__).parent.parent / "skills"


SKILLS_DIR = _get_skills_dir()


@dataclass
class SkillCredentialConfig:
    key: str
    label: str
    placeholder: str = ""
    help: str = ""
    optional: bool = False
    type: str = "text"
    options: list[dict[str, str]] = field(default_factory=list)
    default: str = ""
    depends_on: dict[str, str] = field(default_factory=dict)


@dataclass
class SkillApiConfig:
    base_url: str
    type: str = "rest"
    auth_type: str = "bearer"
    headers: dict[str, str] = field(default_factory=dict)
    domain: str = ""
    aws_service: str = ""

    @property
    def allowed_domains(self) -> list[str]:
        if self.type == "aws":
            return []
        parsed = urlparse(self.base_url)
        return [parsed.netloc] if parsed.netloc else []

    def is_allowed_host(self, host: str) -> bool:
        if self.type == "aws":
            return True
        parsed = urlparse(self.base_url)
        return host == parsed.netloc


@dataclass
class SkillConfig:
    name: str
    display_name: str
    description: str
    emoji: str
    homepage: str
    api: SkillApiConfig
    credentials: list[SkillCredentialConfig]
    docs: str = ""

    @property
    def required_credentials(self) -> list[str]:
        return [c.key for c in self.credentials]


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from SKILL.md content."""
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    try:
        frontmatter = yaml.safe_load(parts[1]) or {}
        docs = parts[2].strip()
        return frontmatter, docs
    except yaml.YAMLError as e:
        logger.error(f"Error parsing YAML frontmatter: {e}")
        return {}, content


def _parse_skill_config(frontmatter: dict[str, Any], docs: str) -> SkillConfig | None:
    """Parse frontmatter dict into SkillConfig."""
    if not frontmatter.get("name"):
        return None

    api_data = frontmatter.get("api", {})
    auth_data = api_data.get("auth", {})

    api_config = SkillApiConfig(
        base_url=api_data.get("base_url", ""),
        type=api_data.get("type", "rest"),
        auth_type=auth_data.get("type", "bearer"),
        headers=api_data.get("headers", {}),
        domain=api_data.get("domain", ""),
        aws_service=api_data.get("aws_service", ""),
    )

    credentials = [
        SkillCredentialConfig(
            key=cred.get("key", "api_key"),
            label=cred.get("label", "API Key"),
            placeholder=cred.get("placeholder", ""),
            help=cred.get("help", ""),
            optional=cred.get("optional", False),
            type=cred.get("type", "text"),
            options=cred.get("options", []),
            default=cred.get("default", ""),
            depends_on=cred.get("depends_on", {}),
        )
        for cred in frontmatter.get("credentials", [])
    ]

    return SkillConfig(
        name=frontmatter["name"],
        display_name=frontmatter.get("display_name", frontmatter["name"].title()),
        description=frontmatter.get("description", ""),
        emoji=frontmatter.get("emoji", "🔧"),
        homepage=frontmatter.get("homepage", ""),
        api=api_config,
        credentials=credentials,
        docs=docs,
    )


class SkillDiscovery:
    @staticmethod
    @lru_cache(maxsize=1)
    def discover_all() -> dict[str, SkillConfig]:
        """Scan server/skills/*/SKILL.md and parse all skill configurations."""
        skills: dict[str, SkillConfig] = {}

        if not SKILLS_DIR.exists():
            logger.warning(f"Skills directory not found: {SKILLS_DIR}")
            return skills

        for skill_dir in SKILLS_DIR.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            try:
                content = skill_file.read_text()
                frontmatter, docs = _parse_frontmatter(content)
                config = _parse_skill_config(frontmatter, docs)

                if config:
                    skills[config.name] = config
                    logger.debug(f"Discovered skill: {config.name}")
            except Exception as e:
                logger.error(f"Error loading skill from {skill_dir.name}: {e}")

        logger.info(f"Discovered {len(skills)} skills: {list(skills.keys())}")
        return skills

    @staticmethod
    def get_skill_config(skill_name: str) -> SkillConfig | None:
        """Get configuration for a specific skill."""
        skills = SkillDiscovery.discover_all()
        return skills.get(skill_name)

    @staticmethod
    def get_skill_names() -> list[str]:
        """Get list of all discovered skill names."""
        return list(SkillDiscovery.discover_all().keys())

    @staticmethod
    def is_valid_skill(skill_name: str) -> bool:
        """Check if a skill name is valid (discovered)."""
        return skill_name in SkillDiscovery.discover_all()

    @staticmethod
    def clear_cache() -> None:
        """Clear the discovery cache (useful for testing or hot-reload)."""
        SkillDiscovery.discover_all.cache_clear()

    @staticmethod
    def search_skills(query: str, skill_names: list[str]) -> list[dict]:
        """Search skills by keyword within a list of enabled skill names."""
        results = []
        query_lower = query.lower()
        all_skills = SkillDiscovery.discover_all()

        for name in skill_names:
            config = all_skills.get(name)
            if not config:
                continue

            searchable = f"{config.name} {config.display_name} {config.description}".lower()
            if query_lower in searchable:
                results.append(
                    {
                        "name": config.name,
                        "display_name": config.display_name,
                        "description": config.description,
                        "emoji": config.emoji,
                    }
                )

        return results
