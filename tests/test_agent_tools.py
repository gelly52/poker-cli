"""Tests for agent tool risk detection."""
from pathlib import Path

from poker.capabilities.scan.detectors.agent_tools import AgentToolDetector


def _rule_ids(content: str) -> set[str]:
    findings = AgentToolDetector().scan(Path("tools.py"), "tools.py", content)
    return {finding.rule_id for finding in findings}


def test_agent_tool_detector_finds_shell_execution() -> None:
    content = """
import os
from langchain.tools import tool

@tool
def run_command(command: str) -> str:
    return os.system(command)
"""

    rule_ids = _rule_ids(content)

    assert "agent-tool-shell-execution" in rule_ids
    assert "agent-tool-missing-confirmation" in rule_ids


def test_agent_tool_detector_finds_file_write() -> None:
    content = """
from pathlib import Path
from langchain.tools import tool

@tool
def write_note(path: str, content: str) -> str:
    Path(path).write_text(content)
    return "ok"
"""

    rule_ids = _rule_ids(content)

    assert "agent-tool-file-write" in rule_ids
    assert "agent-tool-missing-confirmation" in rule_ids


def test_agent_tool_detector_finds_network_access() -> None:
    content = """
import requests
from langchain.tools import tool

@tool
def fetch_url(url: str) -> str:
    return requests.get(url).text
"""

    rule_ids = _rule_ids(content)

    assert "agent-tool-network-access" in rule_ids
    assert "agent-tool-missing-confirmation" in rule_ids


def test_agent_tool_detector_ignores_non_tool_function() -> None:
    content = """
import os

def run_command(command: str) -> str:
    return os.system(command)
"""

    assert _rule_ids(content) == set()


def test_agent_tool_detector_does_not_report_confirmation_when_present() -> None:
    content = """
import subprocess
from langchain.tools import tool

@tool
def run_command(command: str, confirm: bool) -> str:
    if not confirm:
        return "permission required"
    return subprocess.run(command).stdout
"""

    rule_ids = _rule_ids(content)

    assert "agent-tool-shell-execution" in rule_ids
    assert "agent-tool-missing-confirmation" not in rule_ids
