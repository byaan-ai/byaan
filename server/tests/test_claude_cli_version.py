import re
import subprocess
from pathlib import Path

import claude_agent_sdk
import pytest

from server.constants.models import CLAUDE_CODE_MIN_CLI_VERSION

BUNDLED_CLI = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.search(r"\d+\.\d+\.\d+", text).group().split("."))


@pytest.mark.skipif(not BUNDLED_CLI.exists(), reason="claude-agent-sdk wheel for this platform ships no CLI")
def test_bundled_cli_supports_catalog_models():
    output = subprocess.run([str(BUNDLED_CLI), "--version"], capture_output=True, text=True, timeout=60).stdout
    assert _version(output) >= _version(CLAUDE_CODE_MIN_CLI_VERSION), (
        f"claude-agent-sdk bundles Claude Code {output.strip()}, but the Claude models need "
        f"{CLAUDE_CODE_MIN_CLI_VERSION}+. Bump claude-agent-sdk and its exclude-newer-package date in pyproject.toml."
    )
