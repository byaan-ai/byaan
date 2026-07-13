"""Model-call engines for the eval harness.

Three backends behind one `complete()` contract:

- ``litellm``    — API completion via litellm (the original code path).
- ``codex``      — OpenAI Codex CLI (`codex exec`), authenticated locally, no API key.
- ``claude-cli`` — Claude Code CLI (`claude -p`), authenticated locally, no API key.

CLI engines run as subprocesses with a hard timeout (killed on expiry) and are
isolated from any project context: they run with ``cwd=/tmp`` and, for Claude,
with MCP servers, local settings and tools disabled so the run is a pure text
completion.

Two additional *agentic* engines (``claude-agentic`` / ``codex-agentic``) instead
give the CLI agent a per-case workspace containing a copy of the SQLite database
and let it answer by exploring/querying the db with its own tools. These measure
the model-in-CLI-harness ceiling, isolated the same way (no MCP, no project
settings) but with the Bash / read-only sandbox enabled.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

_CODEX_FALLBACK = os.path.expanduser("~/.nvm/versions/node/v24.18.0/bin/codex")
_CLAUDE_FALLBACK = os.path.expanduser("~/.local/bin/claude")

# Claude Code built-in tools disabled so eval runs are pure text completions.
_CLAUDE_DISABLED_TOOLS = [
    "Bash",
    "Edit",
    "Write",
    "Read",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
    "Task",
]

# Agentic Claude runs keep everything disabled except Bash (so it can run sqlite3).
_CLAUDE_AGENTIC_DISABLED_TOOLS = [t for t in _CLAUDE_DISABLED_TOOLS if t != "Bash"]


def _make_workspace(db_path: str) -> str:
    """Create a temp workspace holding a private copy of the eval db as ``eval_data.db``.

    A copy (not a hardlink) is used so an agent with write-capable tools can never
    mutate the shared source database.
    """
    workspace = tempfile.mkdtemp(prefix="agentic_eval_")
    shutil.copy2(db_path, os.path.join(workspace, "eval_data.db"))
    return workspace


@dataclass
class EngineResponse:
    text: str
    latency_s: float
    usage: dict | None = None
    raw_error: str | None = None


class Engine:
    """Base engine. ``max_concurrency`` caps parallel calls (None = uncapped)."""

    name: str = "engine"
    max_concurrency: int | None = None
    # Agentic engines answer by exploring a db workspace with their own tools rather
    # than emitting a one-shot text completion; the runner treats them differently.
    is_agentic: bool = False

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 300.0,
    ) -> EngineResponse:
        raise NotImplementedError


class LitellmEngine(Engine):
    name = "litellm"
    max_concurrency = None

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 300.0,
    ) -> EngineResponse:
        import litellm

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        kwargs: dict = {"model": model, "messages": messages, "temperature": 0, "timeout": timeout}
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        started = time.time()
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return EngineResponse(text="", latency_s=round(time.time() - started, 3), raw_error=str(exc))
        latency = time.time() - started
        content = response.choices[0].message.content or ""
        usage: dict | None = None
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
            }
        return EngineResponse(text=content, latency_s=round(latency, 3), usage=usage)


def _combine_prompt(system_prompt: str, user_prompt: str) -> str:
    return f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\n----- END SYSTEM INSTRUCTIONS -----\n\nUSER:\n{user_prompt}"


class CodexEngine(Engine):
    name = "codex"
    max_concurrency = 3

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("codex") or _CODEX_FALLBACK

    def build_command(self, model: str, reasoning_effort: str | None, outfile: str, prompt: str) -> list[str]:
        cmd = [self.binary, "exec", "--model", model]
        if reasoning_effort:
            cmd += ["-c", f"model_reasoning_effort={reasoning_effort}"]
        cmd += ["--sandbox", "read-only", "--skip-git-repo-check", "-o", outfile, prompt]
        return cmd

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 300.0,
    ) -> EngineResponse:
        prompt = _combine_prompt(system_prompt, user_prompt)
        fd, outfile = tempfile.mkstemp(prefix="codex_eval_", suffix=".txt")
        os.close(fd)
        cmd = self.build_command(model, reasoning_effort, outfile, prompt)
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd="/tmp",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return EngineResponse(
                text="", latency_s=round(time.time() - started, 3), raw_error=f"codex timeout after {timeout}s"
            )
        except Exception as exc:  # noqa: BLE001
            return EngineResponse(text="", latency_s=round(time.time() - started, 3), raw_error=str(exc))
        latency = time.time() - started
        try:
            text = open(outfile).read().strip()
        except OSError:
            text = ""
        finally:
            try:
                os.unlink(outfile)
            except OSError:
                pass
        if not text:
            err = (proc.stderr or "").strip()[-400:]
            return EngineResponse(
                text="", latency_s=round(latency, 3), raw_error=f"codex empty output (rc={proc.returncode}): {err}"
            )
        return EngineResponse(text=text, latency_s=round(latency, 3), usage=None)


class ClaudeCliEngine(Engine):
    name = "claude-cli"
    max_concurrency = 3

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("claude") or _CLAUDE_FALLBACK

    def build_command(self, model: str, reasoning_effort: str | None, system_prompt: str) -> list[str]:
        cmd = [
            self.binary,
            "-p",
            "--model",
            model,
            "--system-prompt",
            system_prompt,
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
        ]
        if reasoning_effort:
            cmd += ["--effort", reasoning_effort]
        cmd += ["--disallowedTools", *_CLAUDE_DISABLED_TOOLS]
        return cmd

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 300.0,
    ) -> EngineResponse:
        cmd = self.build_command(model, reasoning_effort, system_prompt)
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd="/tmp",
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return EngineResponse(
                text="", latency_s=round(time.time() - started, 3), raw_error=f"claude timeout after {timeout}s"
            )
        except Exception as exc:  # noqa: BLE001
            return EngineResponse(text="", latency_s=round(time.time() - started, 3), raw_error=str(exc))
        latency = time.time() - started
        text = (proc.stdout or "").strip()
        if not text:
            err = (proc.stderr or "").strip()[-400:]
            return EngineResponse(
                text="", latency_s=round(latency, 3), raw_error=f"claude empty output (rc={proc.returncode}): {err}"
            )
        return EngineResponse(text=text, latency_s=round(latency, 3), usage=None)


class ClaudeAgenticEngine(Engine):
    """Claude Code CLI answering by exploring a db workspace with the Bash tool only."""

    name = "claude-agentic"
    max_concurrency = 4
    is_agentic = True
    hard_timeout = 300.0

    def __init__(self, db_path: str | None = None, binary: str | None = None) -> None:
        self.db_path = db_path
        self.binary = binary or shutil.which("claude") or _CLAUDE_FALLBACK

    def build_command(self, model: str, reasoning_effort: str | None, user_prompt: str) -> list[str]:
        cmd = [
            self.binary,
            "-p",
            user_prompt,
            "--model",
            model,
            "--output-format",
            "text",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "",
            "--permission-mode",
            "bypassPermissions",
            "--allowedTools",
            "Bash",
        ]
        if reasoning_effort:
            cmd += ["--effort", reasoning_effort]
        cmd += ["--disallowedTools", *_CLAUDE_AGENTIC_DISABLED_TOOLS]
        return cmd

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 300.0,
    ) -> EngineResponse:
        if not self.db_path:
            return EngineResponse(text="", latency_s=0.0, raw_error="claude-agentic: no db_path configured")
        workspace = _make_workspace(self.db_path)
        cmd = self.build_command(model, reasoning_effort, user_prompt)
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                input="",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return EngineResponse(
                text="", latency_s=round(time.time() - started, 3), raw_error=f"claude timeout after {timeout}s"
            )
        except Exception as exc:  # noqa: BLE001
            return EngineResponse(text="", latency_s=round(time.time() - started, 3), raw_error=str(exc))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        latency = time.time() - started
        text = (proc.stdout or "").strip()
        if not text:
            err = (proc.stderr or "").strip()[-400:]
            return EngineResponse(
                text="", latency_s=round(latency, 3), raw_error=f"claude empty output (rc={proc.returncode}): {err}"
            )
        return EngineResponse(text=text, latency_s=round(latency, 3), usage=None)


class CodexAgenticEngine(Engine):
    """OpenAI Codex CLI answering by exploring a db workspace under a read-only sandbox."""

    name = "codex-agentic"
    max_concurrency = 4
    is_agentic = True
    hard_timeout = 420.0

    def __init__(self, db_path: str | None = None, binary: str | None = None) -> None:
        self.db_path = db_path
        self.binary = binary or shutil.which("codex") or _CODEX_FALLBACK

    def build_command(
        self, model: str, reasoning_effort: str | None, workspace: str, outfile: str, prompt: str
    ) -> list[str]:
        cmd = [self.binary, "exec", "--skip-git-repo-check", "-s", "read-only", "-C", workspace, "-o", outfile]
        cmd += ["-c", f"model_reasoning_effort={reasoning_effort or 'high'}"]
        if model:
            cmd += ["--model", model]
        cmd += [prompt]
        return cmd

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str,
        reasoning_effort: str | None = None,
        timeout: float = 420.0,
    ) -> EngineResponse:
        if not self.db_path:
            return EngineResponse(text="", latency_s=0.0, raw_error="codex-agentic: no db_path configured")
        workspace = _make_workspace(self.db_path)
        fd, outfile = tempfile.mkstemp(prefix="codex_agentic_", suffix=".txt")
        os.close(fd)
        cmd = self.build_command(model, reasoning_effort, workspace, outfile, user_prompt)
        started = time.time()
        try:
            proc = subprocess.run(
                cmd,
                cwd=workspace,
                input="",
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if os.path.exists(outfile):
                os.unlink(outfile)
            return EngineResponse(
                text="", latency_s=round(time.time() - started, 3), raw_error=f"codex timeout after {timeout}s"
            )
        except Exception as exc:  # noqa: BLE001
            return EngineResponse(text="", latency_s=round(time.time() - started, 3), raw_error=str(exc))
        finally:
            shutil.rmtree(workspace, ignore_errors=True)
        latency = time.time() - started
        try:
            text = open(outfile).read().strip()
        except OSError:
            text = ""
        finally:
            try:
                os.unlink(outfile)
            except OSError:
                pass
        if not text:
            err = (proc.stderr or "").strip()[-400:]
            return EngineResponse(
                text="", latency_s=round(latency, 3), raw_error=f"codex empty output (rc={proc.returncode}): {err}"
            )
        return EngineResponse(text=text, latency_s=round(latency, 3), usage=None)


_ENGINES: dict[str, type[Engine]] = {
    "litellm": LitellmEngine,
    "codex": CodexEngine,
    "claude-cli": ClaudeCliEngine,
    "claude-agentic": ClaudeAgenticEngine,
    "codex-agentic": CodexAgenticEngine,
}

ENGINE_CHOICES = list(_ENGINES)

_AGENTIC_ENGINES = {"claude-agentic", "codex-agentic"}


def get_engine(name: str, db_path: str | None = None) -> Engine:
    try:
        cls = _ENGINES[name]
    except KeyError:
        raise ValueError(f"unknown engine: {name!r} (choices: {', '.join(ENGINE_CHOICES)})") from None
    if name in _AGENTIC_ENGINES:
        return cls(db_path=db_path)
    return cls()
