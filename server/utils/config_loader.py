import json
import sys
from pathlib import Path
from typing import Any

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)


def get_resource_path(relative_path: str) -> Path | None:
    """
    Get absolute path to a resource file, works for both dev and PyInstaller.

    Args:
        relative_path: Path relative to server directory (e.g., "example_data/demo_notebooks.json")

    Returns:
        Path to the resource file, or None if not found
    """
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            # Running in PyInstaller bundle
            resource_path = Path(sys._MEIPASS) / relative_path
            logger.debug(f"Looking for resource in PyInstaller bundle: {resource_path}")
        else:
            # Running in development
            resource_path = Path(__file__).parent.parent / relative_path
            logger.debug(f"Looking for resource in development: {resource_path}")

        if resource_path.exists():
            return resource_path
        else:
            logger.warning(f"Resource not found at {resource_path}")
            return None
    except Exception as e:
        logger.error(
            f"Failed to get resource path for {relative_path}: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_resource_path", "relative_path": relative_path},
        )
        return None


def get_config_path() -> Path | None:
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            config_path = Path(sys._MEIPASS) / "config.json"
            logger.debug(f"Looking for config.json in PyInstaller bundle: {config_path}")

            if config_path.exists():
                return config_path
            else:
                logger.warning(f"config.json not found in bundle at {config_path}")
                return None
        else:
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "client" / "src-tauri" / "resources" / "config.json"

            logger.debug(f"Looking for config.json in development: {config_path}")

            if config_path.exists():
                return config_path
            else:
                logger.warning(f"config.json not found at {config_path}")
                return None
    except Exception as e:
        logger.error(
            f"Failed to get config path: {str(e)}",
            exc_info=True,
            posthog_context={
                "function": "get_config_path",
                "frozen": getattr(sys, "frozen", False),
            },
        )
        return None


def load_config() -> dict[str, Any]:
    config_path = get_config_path()

    if not config_path:
        logger.warning("No config.json found - using empty configuration")
        return {}

    try:
        with open(config_path) as f:
            config = json.load(f)
            logger.info(f"Configuration loaded from {config_path}")
            return config
    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to parse config.json: {e}",
            exc_info=True,
            posthog_context={
                "function": "load_config",
                "config_path": str(config_path),
                "error_type": "json_decode",
            },
        )
        return {}
    except Exception as e:
        logger.error(
            f"Failed to read config.json: {e}",
            exc_info=True,
            posthog_context={
                "function": "load_config",
                "config_path": str(config_path),
                "error_type": "file_read",
            },
        )
        return {}


def get_posthog_config() -> dict[str, str | None]:
    try:
        config = load_config()
        posthog_config = config.get("posthog", {})

        return {
            "api_key": posthog_config.get("api_key"),
            "host": posthog_config.get("host", "https://us.i.posthog.com"),
        }
    except Exception as e:
        logger.error(
            f"Failed to get PostHog config: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_posthog_config"},
        )
        return {"api_key": None, "host": "https://us.i.posthog.com"}


def get_waitlist_config() -> dict[str, str | None]:
    """
    Get waitlist configuration.

    In frozen Mac/Desktop builds (PyInstaller), reads `worker_url` from the
    bundled `config.json`. In every other deployment (Docker community,
    self-hosted, dev), reads strictly from `WORKER_URL` env so customers can
    enable/disable worker features via `.env` without a baked-in fallback.
    """
    import os

    try:
        if getattr(sys, "frozen", False):
            config = load_config()
            worker_url = config.get("waitlist", {}).get("worker_url") or os.getenv("WORKER_URL")
        else:
            worker_url = os.getenv("WORKER_URL")

        return {"worker_url": worker_url}
    except Exception as e:
        logger.error(
            f"Failed to get waitlist config: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_waitlist_config"},
        )
        return {"worker_url": os.getenv("WORKER_URL")}


def get_openrouter_config() -> dict[str, str | None]:
    """
    Get OpenRouter configuration from config.json or environment variable.
    Prioritizes config.json (for production builds), falls back to .env (for development).
    """
    try:
        import os

        # Try config.json first (production)
        config = load_config()
        openrouter_config = config.get("openrouter", {})
        master_key = openrouter_config.get("master_key")

        # Fall back to environment variable (development)
        if not master_key:
            master_key = os.getenv("OPENROUTER_MASTER_KEY")

        return {
            "master_key": master_key,
        }
    except Exception as e:
        import os

        logger.error(
            f"Failed to get OpenRouter config: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_openrouter_config"},
        )
        # Fall back to environment variable
        return {"master_key": os.getenv("OPENROUTER_MASTER_KEY")}


def get_github_oauth_config() -> dict[str, str | None]:
    try:
        import os

        config = load_config()
        github_config = config.get("github", {})
        client_id = github_config.get("client_id")
        client_secret = github_config.get("client_secret")

        if not client_id:
            client_id = os.getenv("GITHUB_OAUTH_CLIENT_ID")
        if not client_secret:
            client_secret = os.getenv("GITHUB_OAUTH_CLIENT_SECRET")

        return {"client_id": client_id, "client_secret": client_secret}
    except Exception as e:
        import os

        logger.error(
            f"Failed to get GitHub OAuth config: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_github_oauth_config"},
        )
        return {
            "client_id": os.getenv("GITHUB_OAUTH_CLIENT_ID"),
            "client_secret": os.getenv("GITHUB_OAUTH_CLIENT_SECRET"),
        }


def get_deployment_config() -> dict[str, Any]:
    """
    Get deployment configuration from config.json.
    Returns deployment settings including isHosted flag.
    """
    try:
        config = load_config()
        return config.get("deployment", {"isHosted": False})
    except Exception as e:
        logger.error(
            f"Failed to get deployment config: {str(e)}",
            exc_info=True,
            posthog_context={"function": "get_deployment_config"},
        )
        return {"isHosted": False}


def get_app_mode() -> str:
    """
    Get the application running mode. Set explicitly at each entry point:
      - "desktop"   : Mac Tauri DMG app (own onboarding, credit sync, no community setup)
      - "community" : Local Docker via `make local` (auto-create workspace, no auth)
      - "self-hosted": Self-hosted enterprise deployment

    Defaults to "desktop" when not set (PyInstaller binary or local dev).
    """
    import os

    return os.getenv("APP_MODE", "desktop").lower()


def is_desktop_mode() -> bool:
    return get_app_mode() == "desktop"


def is_community_mode() -> bool:
    return get_app_mode() == "community"


def is_self_hosted() -> bool:
    """
    Check if running in self-hosted mode (APP_MODE=self-hosted).

    Returns:
        True if self-hosted mode is active
    """
    return get_app_mode() == "self-hosted"


def get_self_hosted_config() -> dict[str, str | None]:
    """
    Get self-hosted configuration from environment variables.

    Returns:
        Dict with master user configuration
    """
    import os

    return {
        "org_name": os.getenv("ORG_NAME") or os.getenv("TENANT_NAME"),
        "master_email": os.getenv("MASTER_USER_EMAIL"),
        "master_password": os.getenv("MASTER_USER_PASSWORD"),
    }


def validate_self_hosted_config() -> tuple[bool, str | None]:
    """
    Validate that all required self-hosted config is present.

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not is_self_hosted():
        return True, None

    config = get_self_hosted_config()

    if not config["org_name"]:
        return False, "ORG_NAME or TENANT_NAME environment variable is required for self-hosted mode"

    if not config["master_email"]:
        return False, "MASTER_USER_EMAIL environment variable is required for self-hosted mode"

    if not config["master_password"]:
        return False, "MASTER_USER_PASSWORD environment variable is required for self-hosted mode"

    if len(config["master_password"]) < 8:
        return False, "MASTER_USER_PASSWORD must be at least 8 characters"

    return True, None


def get_app_secret() -> str:
    import os

    return os.getenv("APP_SECRET", "change-me-in-production-use-strong-secret")


def get_auth_secret() -> str:
    import os

    # Prefer the single APP_SECRET for JWT signing
    app_secret = os.getenv("APP_SECRET")
    if app_secret:
        return app_secret

    # Legacy fallback for older deployments
    legacy = os.getenv("AUTH_SECRET")
    if legacy:
        return legacy

    # Final fallback to JWT_SECRET if still configured
    jwt_secret = os.getenv("JWT_SECRET")
    if jwt_secret:
        return jwt_secret

    return get_app_secret()


def get_email_config() -> dict[str, str | None]:
    import os

    return {
        "api_key": os.getenv("RESEND_API_KEY"),
        "from_email": os.getenv("FROM_EMAIL", "noreply@byaan.ai"),
        "frontend_url": os.getenv("FRONTEND_URL", "http://localhost:5173"),
    }


def get_google_oauth_config() -> dict[str, str | None]:
    """
    Get Google OAuth configuration from environment variables.
    """
    import os

    return {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
    }


def get_token_config() -> dict[str, int]:
    import os

    return {
        "access_token_lifetime_seconds": int(os.getenv("ACCESS_TOKEN_LIFETIME_SECONDS", "1800")),  # 30 minutes
        "refresh_token_lifetime_seconds": int(os.getenv("REFRESH_TOKEN_LIFETIME_SECONDS", "86400")),  # 1 day
    }


def should_hide_email_auth() -> bool:
    """
    Check if email/password authentication should be hidden.
    """
    import os

    hide_email_env = os.getenv("HIDE_EMAIL_AUTH", "").lower()
    if hide_email_env not in ("1", "true", "yes"):
        return False

    google_config = get_google_oauth_config()
    return bool(google_config.get("client_id"))


def get_smtp_config() -> dict[str, str | bool | int | None]:
    """
    Get SMTP configuration from environment variables.

    Returns:
        Dict with SMTP settings or empty dict if not configured.
        All values must be explicitly set by the user except SMTP_USE_TLS (defaults to true).
    """
    import os

    smtp_host = os.getenv("SMTP_HOST")
    if not smtp_host:
        return {}

    smtp_port = os.getenv("SMTP_PORT")
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_from_email = os.getenv("SMTP_FROM_EMAIL")
    smtp_from_name = os.getenv("SMTP_FROM_NAME")

    # Validate required fields
    if not smtp_port or not smtp_username or not smtp_password or not smtp_from_email:
        logger.error(
            "SMTP configuration incomplete. Required: SMTP_HOST, SMTP_PORT, "
            "SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM_EMAIL"
        )
        return {}

    try:
        smtp_port = int(smtp_port)
    except ValueError:
        logger.error(f"Invalid SMTP_PORT value: {smtp_port}. Must be a number.")
        return {}

    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")

    return {
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_username": smtp_username,
        "smtp_password": smtp_password,
        "smtp_from_email": smtp_from_email,
        "smtp_from_name": smtp_from_name,
        "smtp_use_tls": smtp_use_tls,
    }


def get_skill_loop_config() -> dict[str, bool | int]:
    """
    Get skill-learning-loop configuration from environment variables.

    The loop is on by default; SKILL_LOOP_ENABLED=false acts as an emergency kill-switch.

    Returns:
        Dict with enabled flag, tick interval, per-day evaluation cap, and digest hour.
    """
    import os

    enabled = os.getenv("SKILL_LOOP_ENABLED", "true").lower() not in ("0", "false", "no")

    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except ValueError:
            logger.warning(f"Invalid {name} value, falling back to {default}")
            return default

    code_sync_enabled = os.getenv("SKILL_LOOP_CODE_SYNC_ENABLED", "true").lower() not in ("0", "false", "no")

    return {
        "enabled": enabled,
        "interval_seconds": _int("SKILL_LOOP_INTERVAL_SECONDS", 1800),
        "max_evals_per_day": _int("SKILL_LOOP_MAX_EVALS_PER_DAY", 20),
        "digest_hour": _int("SKILL_LOOP_DIGEST_HOUR", 17),
        "code_sync_enabled": code_sync_enabled,
        "code_sync_interval_seconds": _int("SKILL_LOOP_CODE_SYNC_INTERVAL_SECONDS", 86400),
        "code_sessions_per_day": _int("SKILL_LOOP_CODE_SESSIONS_PER_DAY", 10),
        "code_max_skills_per_tick": _int("SKILL_LOOP_CODE_MAX_SKILLS_PER_TICK", 3),
        "agent_timeout_seconds": _int("SKILL_LOOP_AGENT_TIMEOUT_SECONDS", 300),
    }


def get_public_base_url() -> str:
    """
    Get public base URL for generating shared dashboard links.

    Returns:
        Public base URL (e.g., https://app.byaan.ai or http://localhost:8000)
    """
    import os

    return os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")
