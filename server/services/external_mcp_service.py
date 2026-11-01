from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from server.models.external_mcp_server import ExternalMCPServer
from server.repositories.external_mcp_servers import ExternalMCPServerRepository
from server.utils.config_loader import is_self_hosted
from server.utils.custom_logger import get_logger
from server.utils.general import build_expanded_path

logger = get_logger(__name__)

CLIENT_SESSION_TIMEOUT_SECONDS = 30
TEST_CONNECTION_TIMEOUT_SECONDS = 15


def _tool_pattern(server_name: str) -> str:
    return f"mcp__{server_name}__*"


def _stdio_env_with_path(env: dict[str, str] | None) -> dict[str, str]:
    base = dict(env or {})
    base.setdefault("PATH", build_expanded_path())
    return base


_METADATA_HOSTS = {
    "169.254.169.254",
    "fd00:ec2::254",
    "metadata.google.internal",
    "metadata.goog",
}


def _validate_remote_url(url: str) -> str | None:
    """In hosted mode, block SSRF-dangerous URLs. Returns error string if invalid, else None."""
    if not is_self_hosted():
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return "Invalid URL"

    if parsed.scheme not in ("http", "https"):
        return "Only http/https URLs are allowed"

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return "URL must have a hostname"

    if host in _METADATA_HOSTS:
        return "Cloud metadata endpoints are not allowed"

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return f"Hostname '{host}' could not be resolved"

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return (
                f"URL resolves to a non-public IP ({ip_str}); private, loopback, and link-local addresses are blocked"
            )
    return None


class ExternalMCPService:
    """Load, validate and expose external MCP server configs for both agent paths."""

    @staticmethod
    def is_stdio_allowed() -> bool:
        return not is_self_hosted()

    @staticmethod
    async def _load_enabled(session: AsyncSession) -> list[tuple[ExternalMCPServer, dict[str, Any]]]:
        repo = ExternalMCPServerRepository(session)
        records = await repo.list_enabled()
        out: list[tuple[ExternalMCPServer, dict[str, Any]]] = []
        for rec in records:
            try:
                decrypted = await repo.decrypt_config(rec)
                out.append((rec, decrypted))
            except Exception as exc:
                logger.error(
                    f"Failed to decrypt external MCP server {rec.id} ({rec.name}); "
                    f"the stored secret cannot be read with the current encryption key — "
                    f"re-enter credentials to recover: {exc}"
                )
        return out

    @staticmethod
    async def decrypt_safely(
        repo: ExternalMCPServerRepository, record: ExternalMCPServer
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Attempt to decrypt a server's config. Returns (config, None) on success, (None, error) on failure."""
        try:
            return await repo.decrypt_config(record), None
        except Exception as exc:
            logger.error(f"Decrypt failed for external MCP {record.id} ({record.name}): {exc}")
            return None, "Stored secret cannot be decrypted. Re-enter credentials to recover."

    @classmethod
    async def build_openai_agents_servers(cls, session: AsyncSession) -> list[Any]:
        """Build openai-agents SDK MCPServer instances for the current tenant."""
        from agents.mcp import (
            MCPServerSse,
            MCPServerSseParams,
            MCPServerStdio,
            MCPServerStdioParams,
            MCPServerStreamableHttp,
            MCPServerStreamableHttpParams,
        )

        records = await cls._load_enabled(session)
        built: list[tuple[str, Any]] = []
        for rec, cfg in records:
            try:
                tool_filter = ExternalMCPServerRepository.parse_tool_filter(rec)
                allowed = {"allowed_tool_names": tool_filter} if tool_filter else None
                if rec.transport == "stdio":
                    if not cls.is_stdio_allowed():
                        logger.info(f"Skipping stdio external MCP '{rec.name}' in hosted mode")
                        continue
                    params = MCPServerStdioParams(
                        command=cfg["command"],
                        args=cfg.get("args", []),
                        env=_stdio_env_with_path(cfg.get("env")),
                    )
                    built.append(
                        (
                            rec.name,
                            MCPServerStdio(
                                params=params,
                                cache_tools_list=True,
                                name=rec.name,
                                client_session_timeout_seconds=CLIENT_SESSION_TIMEOUT_SECONDS,
                                tool_filter=allowed,
                            ),
                        )
                    )
                elif rec.transport == "sse":
                    ssrf_err = _validate_remote_url(cfg.get("url", ""))
                    if ssrf_err:
                        logger.warning(f"Skipping external MCP '{rec.name}': {ssrf_err}")
                        continue
                    params = MCPServerSseParams(
                        url=cfg["url"],
                        headers=cfg.get("headers", {}),
                        timeout=cfg.get("timeout_seconds", CLIENT_SESSION_TIMEOUT_SECONDS),
                    )
                    built.append(
                        (
                            rec.name,
                            MCPServerSse(
                                params=params,
                                cache_tools_list=True,
                                name=rec.name,
                                client_session_timeout_seconds=CLIENT_SESSION_TIMEOUT_SECONDS,
                                tool_filter=allowed,
                            ),
                        )
                    )
                elif rec.transport == "streamable_http":
                    ssrf_err = _validate_remote_url(cfg.get("url", ""))
                    if ssrf_err:
                        logger.warning(f"Skipping external MCP '{rec.name}': {ssrf_err}")
                        continue
                    params = MCPServerStreamableHttpParams(
                        url=cfg["url"],
                        headers=cfg.get("headers", {}),
                        timeout=cfg.get("timeout_seconds", CLIENT_SESSION_TIMEOUT_SECONDS),
                    )
                    built.append(
                        (
                            rec.name,
                            MCPServerStreamableHttp(
                                params=params,
                                cache_tools_list=True,
                                name=rec.name,
                                client_session_timeout_seconds=CLIENT_SESSION_TIMEOUT_SECONDS,
                                tool_filter=allowed,
                            ),
                        )
                    )
                else:
                    logger.warning(f"Unknown transport '{rec.transport}' for MCP '{rec.name}'")
            except Exception as exc:
                logger.warning(f"Failed to build openai-agents server for '{rec.name}': {exc}")

        connected: list[Any] = []
        for name, server in built:
            try:
                await server.connect()
                connected.append(server)
                logger.info(f"Connected to external MCP '{name}'")
            except Exception as exc:
                logger.warning(f"Failed to connect external MCP '{name}': {exc}")
                try:
                    await server.cleanup()
                except Exception:
                    pass
        return connected

    @classmethod
    async def cleanup_openai_agents_servers(cls, servers: list[Any]) -> None:
        """Cleanup connected MCP server instances. Safe to call with empty list."""
        for server in servers or []:
            try:
                await server.cleanup()
            except Exception as exc:
                logger.warning(f"Error cleaning up MCP server: {exc}")

    @classmethod
    async def build_claude_mcp_dict(cls, session: AsyncSession) -> tuple[dict[str, dict[str, Any]], list[str]]:
        """Return (mcp_servers_dict_additions, allowed_tool_patterns) for Claude SDK."""
        records = await cls._load_enabled(session)
        servers_dict: dict[str, dict[str, Any]] = {}
        allowed_patterns: list[str] = []
        for rec, cfg in records:
            try:
                if rec.transport == "stdio":
                    if not cls.is_stdio_allowed():
                        continue
                    servers_dict[rec.name] = {
                        "type": "stdio",
                        "command": cfg["command"],
                        "args": cfg.get("args", []),
                        "env": _stdio_env_with_path(cfg.get("env")),
                    }
                elif rec.transport == "sse":
                    ssrf_err = _validate_remote_url(cfg.get("url", ""))
                    if ssrf_err:
                        logger.warning(f"Skipping external MCP '{rec.name}': {ssrf_err}")
                        continue
                    entry: dict[str, Any] = {"type": "sse", "url": cfg["url"]}
                    if cfg.get("headers"):
                        entry["headers"] = cfg["headers"]
                    servers_dict[rec.name] = entry
                elif rec.transport == "streamable_http":
                    ssrf_err = _validate_remote_url(cfg.get("url", ""))
                    if ssrf_err:
                        logger.warning(f"Skipping external MCP '{rec.name}': {ssrf_err}")
                        continue
                    entry = {"type": "http", "url": cfg["url"]}
                    if cfg.get("headers"):
                        entry["headers"] = cfg["headers"]
                    servers_dict[rec.name] = entry
                else:
                    continue

                tool_filter = ExternalMCPServerRepository.parse_tool_filter(rec)
                if tool_filter:
                    for tool_name in tool_filter:
                        allowed_patterns.append(f"mcp__{rec.name}__{tool_name}")
                else:
                    allowed_patterns.append(_tool_pattern(rec.name))
            except Exception as exc:
                logger.warning(f"Failed to build Claude MCP entry for '{rec.name}': {exc}")
        return servers_dict, allowed_patterns

    @classmethod
    async def test_connection(cls, transport: str, config: dict[str, Any]) -> tuple[bool, list[str], str | None]:
        """Connect to an MCP server, list tools, disconnect. Returns (success, tool_names, error)."""
        from agents.mcp import (
            MCPServerSse,
            MCPServerSseParams,
            MCPServerStdio,
            MCPServerStdioParams,
            MCPServerStreamableHttp,
            MCPServerStreamableHttpParams,
        )

        server: Any = None
        try:
            if transport == "stdio":
                if not cls.is_stdio_allowed():
                    return False, [], "stdio transport is disabled in hosted mode"
                server = MCPServerStdio(
                    params=MCPServerStdioParams(
                        command=config["command"],
                        args=config.get("args", []),
                        env=_stdio_env_with_path(config.get("env")),
                    ),
                    cache_tools_list=False,
                    name="test",
                    client_session_timeout_seconds=TEST_CONNECTION_TIMEOUT_SECONDS,
                )
            elif transport == "sse":
                ssrf_err = _validate_remote_url(config.get("url", ""))
                if ssrf_err:
                    return False, [], ssrf_err
                server = MCPServerSse(
                    params=MCPServerSseParams(
                        url=config["url"],
                        headers=config.get("headers", {}),
                        timeout=config.get("timeout_seconds", TEST_CONNECTION_TIMEOUT_SECONDS),
                    ),
                    cache_tools_list=False,
                    name="test",
                    client_session_timeout_seconds=TEST_CONNECTION_TIMEOUT_SECONDS,
                )
            elif transport == "streamable_http":
                ssrf_err = _validate_remote_url(config.get("url", ""))
                if ssrf_err:
                    return False, [], ssrf_err
                server = MCPServerStreamableHttp(
                    params=MCPServerStreamableHttpParams(
                        url=config["url"],
                        headers=config.get("headers", {}),
                        timeout=config.get("timeout_seconds", TEST_CONNECTION_TIMEOUT_SECONDS),
                    ),
                    cache_tools_list=False,
                    name="test",
                    client_session_timeout_seconds=TEST_CONNECTION_TIMEOUT_SECONDS,
                )
            else:
                return False, [], f"Unknown transport '{transport}'"

            await asyncio.wait_for(server.connect(), timeout=TEST_CONNECTION_TIMEOUT_SECONDS)
            tools = await asyncio.wait_for(server.list_tools(), timeout=TEST_CONNECTION_TIMEOUT_SECONDS)
            tool_names = [t.name for t in tools]
            return True, tool_names, None
        except TimeoutError:
            return False, [], f"Connection timed out after {TEST_CONNECTION_TIMEOUT_SECONDS}s"
        except Exception as exc:
            return False, [], str(exc)
        finally:
            if server is not None:
                try:
                    await server.cleanup()
                except Exception:
                    pass

    @staticmethod
    def mask_response_config(transport: str, decrypted: dict[str, Any]) -> dict[str, Any]:
        """Mask secrets in a decrypted config for API responses."""
        masked = dict(decrypted)
        if transport == "stdio":
            env = masked.get("env", {}) or {}
            masked["env"] = {
                k: ("***" if any(s in k.upper() for s in ("TOKEN", "KEY", "SECRET", "PASSWORD")) else v)
                for k, v in env.items()
            }
        else:
            headers = masked.get("headers", {}) or {}
            masked["headers"] = {
                k: (
                    "***"
                    if k.lower() in {"authorization", "x-api-key", "api-key"}
                    or "token" in k.lower()
                    or "secret" in k.lower()
                    else v
                )
                for k, v in headers.items()
            }
        return masked
