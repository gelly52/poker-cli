"""Tests for poker.capabilities.audit.tools."""
from pathlib import Path

import pytest

from poker.capabilities.audit.tools import audit_tool, find_tools


SAMPLE_TOOL_FILE = '''
from langchain_core.tools import tool


@tool
def search_files(query: str) -> str:
    """Search files matching query."""
    import subprocess
    result = subprocess.run(f"find . -name '{query}'", shell=True, capture_output=True, text=True)
    return result.stdout


@tool
def safe_calc(a: int, b: int) -> int:
    """Add two ints with explicit isinstance validation and clear contract."""
    assert isinstance(a, int)
    assert isinstance(b, int)
    return a + b


def regular_function(x: str) -> str:
    """Not a @tool — should be ignored by find_tools."""
    return x.upper()
'''


@pytest.fixture
def project_with_tools(tmp_path):
    f = tmp_path / "agent.py"
    f.write_text(SAMPLE_TOOL_FILE, encoding="utf-8")
    return tmp_path


def test_find_tools_detects_tool_decorator(project_with_tools):
    tools = find_tools(project_with_tools)
    names = {t.name for t in tools}
    assert names == {"search_files", "safe_calc"}


def test_find_tools_skips_non_tool_functions(project_with_tools):
    tools = find_tools(project_with_tools)
    assert "regular_function" not in {t.name for t in tools}


def test_audit_tool_flags_shell_true(project_with_tools):
    tools = find_tools(project_with_tools)
    target = next(t for t in tools if t.name == "search_files")
    result = audit_tool(target, llm=None)
    checks = {r.check for r in result.risks}
    assert "shell_exec" in checks
    assert result.overall_severity in ("high", "critical")


def test_audit_tool_handles_safe_function(project_with_tools):
    tools = find_tools(project_with_tools)
    target = next(t for t in tools if t.name == "safe_calc")
    result = audit_tool(target, llm=None)
    checks = {r.check for r in result.risks}
    # 不应有 shell_exec / dynamic_exec
    assert "shell_exec" not in checks
    assert "dynamic_exec" not in checks


def test_find_tools_returns_relative_path(project_with_tools):
    tools = find_tools(project_with_tools)
    assert all("/" not in t.file or not t.file.startswith("/") for t in tools)
    assert any(t.file == "agent.py" for t in tools)
