"""
Claude OAuth PKCE Service
-------------------------
Implements OAuth 2.0 with PKCE for Claude Pro/Max subscription authentication.
This allows users to authenticate with their Claude subscription from within Byaan.

Supports two authentication methods:
1. OAuth flow - Browser-based, token expires in ~1 day
2. Long-term token - User runs `claude setup-token` and pastes token (lasts ~1 year)
"""

import asyncio
import base64
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx

from server.utils.custom_logger import get_logger

logger = get_logger(__name__)

AuthMethod = Literal["oauth", "token", None]

_refresh_lock = asyncio.Lock()


@asynccontextmanager
async def _credentials_file_lock():
    """Serialize refreshes across worker processes; a rotated refresh token must never be spent twice."""
    lock_path = Path.home() / ".claude" / ".byaan-refresh.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(lock_path, "w") as handle:
        await asyncio.to_thread(fcntl.flock, handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# Claude OAuth Configuration (same as Claude Code CLI)
CLAUDE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CLAUDE_AUTH_URL = "https://claude.ai/oauth/authorize"
# FIXED: Use /v1/ endpoint (not /api/) - this avoids Cloudflare blocking
CLAUDE_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
CLAUDE_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
CLAUDE_SCOPES = "org:create_api_key user:profile user:inference"

# In-memory PKCE store (maps state -> code_verifier)
# In production with multiple workers, use Redis or database
_pkce_store: dict[str, str] = {}


def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate PKCE code_verifier and code_challenge.

    Returns:
        Tuple of (code_verifier, code_challenge)
    """
    # Generate 32 random bytes -> 43 char base64url string
    code_verifier = secrets.token_urlsafe(32)

    # SHA256 hash, then base64url encode (without padding)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    return code_verifier, code_challenge


def create_auth_url() -> tuple[str, str]:
    """
    Create OAuth authorization URL with PKCE parameters.

    Returns:
        Tuple of (auth_url, state) where state is used to validate the callback
    """
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)

    # Store verifier for later exchange (keyed by state)
    _pkce_store[state] = code_verifier
    logger.info(f"[CLAUDE OAUTH] Created auth URL with state: {state[:16]}...")

    params = {
        "code": "true",
        "client_id": CLAUDE_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": CLAUDE_REDIRECT_URI,
        "scope": CLAUDE_SCOPES,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }

    auth_url = f"{CLAUDE_AUTH_URL}?{urlencode(params)}"
    return auth_url, state


async def exchange_code_for_tokens(code: str, state: str) -> dict:
    """
    Exchange authorization code for access and refresh tokens.

    Args:
        code: Authorization code from OAuth callback
        state: State parameter to validate and retrieve code_verifier

    Returns:
        Token response dict with access_token, refresh_token, expires_in, etc.

    Raises:
        ValueError: If state is invalid/expired or token exchange fails
    """
    code_verifier = _pkce_store.pop(state, None)
    if not code_verifier:
        logger.error(f"[CLAUDE OAUTH] Invalid or expired state: {state[:16]}...")
        raise ValueError("Invalid or expired state parameter. Please restart the authentication flow.")

    # Parse code - may have # suffix from redirect URL
    clean_code = code.split("#")[0] if "#" in code else code

    logger.info("[CLAUDE OAUTH] Exchanging code for tokens...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # FIXED: Use JSON body (not form-urlencoded) - Anthropic uses JSON format
        response = await client.post(
            CLAUDE_TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": clean_code,
                "client_id": CLAUDE_CLIENT_ID,
                "code_verifier": code_verifier,
                "redirect_uri": CLAUDE_REDIRECT_URI,
                "state": state,
            },
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            error_detail = response.text
            logger.error(f"[CLAUDE OAUTH] Token exchange failed: {response.status_code} - {error_detail}")
            raise ValueError(f"Token exchange failed: {error_detail}")

        tokens = response.json()
        logger.info("[CLAUDE OAUTH] Successfully obtained tokens")
        return tokens


def save_credentials(tokens: dict) -> Path:
    """
    Save OAuth tokens to ~/.claude/.credentials.json.

    Args:
        tokens: Token response from exchange_code_for_tokens

    Returns:
        Path to the saved credentials file
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    credentials_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate expiration timestamp (tokens.expires_in is in seconds)
    # Store as milliseconds to match Claude Code CLI format
    expires_at = int(datetime.now().timestamp() * 1000) + (tokens["expires_in"] * 1000)

    credentials = {
        "claudeAiOauth": {
            "accessToken": tokens["access_token"],
            "refreshToken": tokens["refresh_token"],
            "expiresAt": expires_at,
            "scopes": tokens.get("scope", "").split(),
        }
    }

    credentials_path.write_text(json.dumps(credentials, indent=2))
    logger.info(f"[CLAUDE OAUTH] Credentials saved to {credentials_path}")
    return credentials_path


def get_credentials() -> dict | None:
    """
    Load existing OAuth credentials from ~/.claude/.credentials.json.

    Returns:
        Credentials dict or None if not found/invalid
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if not credentials_path.exists():
        return None

    try:
        credentials = json.loads(credentials_path.read_text())
        return credentials.get("claudeAiOauth")
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[CLAUDE OAUTH] Failed to load credentials: {e}")
        return None


def is_token_expired() -> bool:
    """
    Check if the stored access token is expired or about to expire.

    Returns:
        True if expired or expiring within 5 minutes, False otherwise
    """
    credentials = get_credentials()
    if not credentials:
        return True

    expires_at = credentials.get("expiresAt", 0)
    # Check if expired or expiring within 5 minutes (300000 ms)
    current_time_ms = int(datetime.now().timestamp() * 1000)
    return current_time_ms >= (expires_at - 300000)


async def refresh_tokens() -> dict | None:
    """
    Refresh the access token using the stored refresh token.

    Returns:
        New token response or None if refresh fails
    """
    credentials = get_credentials()
    if not credentials or not credentials.get("refreshToken"):
        logger.warning("[CLAUDE OAUTH] No refresh token available")
        return None

    logger.info("[CLAUDE OAUTH] Refreshing access token...")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # FIXED: Use JSON body (not form-urlencoded) - Anthropic uses JSON format
        response = await client.post(
            CLAUDE_TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": credentials["refreshToken"],
                "client_id": CLAUDE_CLIENT_ID,
            },
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            logger.error(f"[CLAUDE OAUTH] Token refresh failed: {response.status_code}")
            return None

        tokens = response.json()
        save_credentials(tokens)
        logger.info("[CLAUDE OAUTH] Token refreshed successfully")
        return tokens


def delete_credentials() -> bool:
    """
    Delete stored OAuth credentials (logout).

    Returns:
        True if credentials were deleted, False if they didn't exist
    """
    credentials_path = Path.home() / ".claude" / ".credentials.json"
    if credentials_path.exists():
        credentials_path.unlink()
        logger.info("[CLAUDE OAUTH] Credentials deleted")
        return True
    return False


def get_long_term_token_path() -> Path:
    """Get path to the long-term token file."""
    return Path.home() / ".claude" / ".byaan_token"


def get_long_term_token() -> str | None:
    """
    Get the stored long-term token.

    Returns:
        Token string or None if not found
    """
    token_path = get_long_term_token_path()
    if token_path.exists():
        try:
            return token_path.read_text().strip()
        except Exception as e:
            logger.warning(f"[CLAUDE OAUTH] Failed to read long-term token: {e}")
    return None


def save_long_term_token(token: str) -> Path:
    """
    Save a long-term token to ~/.claude/.byaan_token.

    Args:
        token: The token obtained from `claude setup-token`

    Returns:
        Path to the saved token file
    """
    token_path = get_long_term_token_path()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token.strip())
    os.chmod(token_path, 0o600)
    logger.info(f"[CLAUDE OAUTH] Long-term token saved to {token_path}")
    return token_path


def delete_long_term_token() -> bool:
    """
    Delete the stored long-term token.

    Returns:
        True if token was deleted, False if it didn't exist
    """
    token_path = get_long_term_token_path()
    if token_path.exists():
        token_path.unlink()
        logger.info("[CLAUDE OAUTH] Long-term token deleted")
        return True
    return False


async def validate_long_term_token(token: str) -> bool:
    """
    Validate a long-term token by making a test API call.

    Args:
        token: The token to validate

    Returns:
        True if the token is valid, False otherwise
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-version": "2023-06-01",
                },
            )
            is_valid = response.status_code == 200
            if is_valid:
                logger.info("[CLAUDE OAUTH] Long-term token validated successfully")
            else:
                logger.warning(f"[CLAUDE OAUTH] Token validation failed: {response.status_code}")
            return is_valid
    except Exception as e:
        logger.error(f"[CLAUDE OAUTH] Token validation error: {e}")
        return False


def get_auth_method() -> AuthMethod:
    """
    Determine which authentication method is currently active.

    Returns:
        'token' if long-term token exists
        'oauth' if OAuth credentials exist
        None if neither exists
    """
    if get_long_term_token():
        return "token"
    if get_credentials():
        return "oauth"
    return None


async def get_active_token_with_refresh() -> str | None:
    """
    Get the currently active token, refreshing OAuth token if expired.

    Returns:
        The active token or None if not authenticated
    """
    long_term = get_long_term_token()
    if long_term:
        return long_term

    if is_token_expired():
        async with _refresh_lock, _credentials_file_lock():
            if is_token_expired():
                logger.info("[CLAUDE OAUTH] Token expired, attempting refresh...")
                refreshed = await refresh_tokens()
                if not refreshed:
                    logger.warning("[CLAUDE OAUTH] Token refresh failed")
                    return None

    credentials = get_credentials()
    if credentials and credentials.get("accessToken"):
        return credentials["accessToken"]

    return None
