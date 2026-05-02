"""Tests for agent tool risk detection."""
from pathlib import Path

import pytest

from poker.agent import tools as agent_tools
from poker.agent.tools import apply_patch, write_file
from poker.capabilities.scan.detectors.agent_tools import AgentToolDetector


def _rule_ids(content: str) -> set[str]:
    findings = AgentToolDetector().scan(Path("tools.py"), "tools.py", content)
    return {finding.rule_id for finding in findings}


def test_agent_tool_detector_finds_command_execution() -> None:
    content = """
import os
from langchain.tools import tool

@tool
def run_command(command: str) -> str:
    return os.system(command)
"""

    rule_ids = _rule_ids(content)

    assert "arbitrary-command-execution" in rule_ids
    assert "agent-tool-missing-confirmation" in rule_ids


def test_agent_tool_detector_finds_file_access() -> None:
    content = """
from pathlib import Path
from langchain.tools import tool

@tool
def read_file(path: str) -> str:
    return Path(path).read_text()
"""

    rule_ids = _rule_ids(content)

    assert "unsafe-file-access" in rule_ids
    assert "agent-tool-missing-confirmation" in rule_ids


def test_agent_tool_detector_finds_ssrf_risk() -> None:
    content = """
import requests
from langchain.tools import tool

@tool
def fetch_url(url: str) -> str:
    return requests.get(url).text
"""

    rule_ids = _rule_ids(content)

    assert "ssrf-risk" in rule_ids
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

    assert "arbitrary-command-execution" in rule_ids
    assert "agent-tool-missing-confirmation" not in rule_ids


# ---------------------------------------------------------------------------
# write_file / apply_patch（Phase 2 写文件能力）
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 Path.home() 重定向到 tmp_path，避免污染真实 ~/.poker。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """初始化 project_root + 默认 confirm=True，单测专用。"""
    root = tmp_path / "proj"
    root.mkdir()
    agent_tools.set_project_root(root)
    # 默认接受 diff（个别测试覆盖）
    monkeypatch.setattr(agent_tools, "show_diff_and_confirm", lambda *a, **kw: True)
    return root


def test_write_file_rejects_out_of_bounds(project, fake_home):
    """越界路径直接拒绝，不触发 diff / 备份逻辑。"""
    result = write_file.invoke({"path": "/etc/passwd", "content": "x"})
    assert result.startswith("错误：路径越界")


def test_apply_patch_rejects_out_of_bounds(project, fake_home):
    result = apply_patch.invoke({"path": "/etc/passwd", "diff": "@@ -1 +1 @@\n-a\n+b\n"})
    assert result.startswith("错误：路径越界")


def test_write_file_creates_backup_on_success(project, fake_home):
    """写入成功时应备份原文件，备份文件名含原文件名。"""
    target = project / "README.md"
    target.write_bytes(b"old\n")

    result = write_file.invoke({"path": "README.md", "content": "new\n"})

    assert result.startswith("已写入")
    assert target.read_bytes().replace(b"\r\n", b"\n") == b"new\n"

    from poker.state import get_state_dir

    backup_dir = get_state_dir(project) / "backups"
    backups = list(backup_dir.iterdir())
    assert len(backups) == 1
    assert "README.md" in backups[0].name
    assert backups[0].read_bytes() == b"old\n"


def test_apply_patch_invalid_diff_keeps_file(project, fake_home):
    """无效 diff（context 不匹配）应返回错误，原文件不动，无备份。"""
    target = project / "main.py"
    original = "print('hi')\n"
    target.write_text(original, encoding="utf-8")

    bad_diff = (
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-print('NOT_MATCHING')\n"
        "+print('bye')\n"
    )
    result = apply_patch.invoke({"path": "main.py", "diff": bad_diff})

    assert result.startswith("错误：diff 应用失败")
    assert target.read_text(encoding="utf-8") == original

    from poker.state import get_state_dir

    backup_dir = get_state_dir(project) / "backups"
    assert not backup_dir.exists() or list(backup_dir.iterdir()) == []


def test_write_file_user_rejects(project, fake_home, monkeypatch):
    """用户拒绝时返回 '用户拒绝'，原文件不变，不创建备份。"""
    target = project / "README.md"
    target.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(agent_tools, "show_diff_and_confirm", lambda *a, **kw: False)

    result = write_file.invoke({"path": "README.md", "content": "new\n"})

    assert result == "用户拒绝"
    assert target.read_text(encoding="utf-8") == "old\n"

    from poker.state import get_state_dir

    backup_dir = get_state_dir(project) / "backups"
    assert not backup_dir.exists() or list(backup_dir.iterdir()) == []


def test_apply_patch_valid_diff_applies(project, fake_home):
    """合法 unified diff 应正确应用并备份。"""
    target = project / "main.py"
    target.write_text("a\nb\nc\n", encoding="utf-8")

    diff = (
        "--- a/main.py\n"
        "+++ b/main.py\n"
        "@@ -1,3 +1,3 @@\n"
        " a\n"
        "-b\n"
        "+B\n"
        " c\n"
    )
    result = apply_patch.invoke({"path": "main.py", "diff": diff})

    assert result.startswith("已应用 patch")
    assert target.read_text(encoding="utf-8") == "a\nB\nc\n"


def test_write_file_creates_new_file_with_empty_backup(project, fake_home):
    """新建文件时备份为 0 字节占位（标记原文件不存在）。"""
    new_path = project / "new.txt"
    assert not new_path.exists()

    result = write_file.invoke({"path": "new.txt", "content": "hello\n"})

    assert result.startswith("已写入")
    assert new_path.read_text(encoding="utf-8") == "hello\n"

    from poker.state import get_state_dir

    backup_dir = get_state_dir(project) / "backups"
    backups = list(backup_dir.iterdir())
    assert len(backups) == 1
    assert backups[0].read_bytes() == b""
