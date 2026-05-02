"""Tests for poker.capabilities.audit.mcp."""
import json

import pytest

from poker.capabilities.audit.mcp import audit_mcp, find_mcp_configs


SAMPLE_JSON = {
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/"],
            "env": {"API_KEY": "sk-leak"},
        },
        "shell": {
            "command": "bash",
            "args": ["-c", "echo dangerous"],
        },
        "wildcard": {
            "command": "/usr/bin/x",
            "tools": ["*"],
        },
    }
}


@pytest.fixture
def mcp_project(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps(SAMPLE_JSON), encoding="utf-8")
    return tmp_path


def test_find_mcp_configs_parses_json(mcp_project):
    infos = find_mcp_configs(mcp_project)
    assert len(infos) == 1
    info = infos[0]
    assert info.kind == "json_config"
    assert {s.name for s in info.servers} == {"filesystem", "shell", "wildcard"}


def test_audit_mcp_flags_shell_wrapper(mcp_project):
    infos = find_mcp_configs(mcp_project)
    result = audit_mcp(infos[0], llm=None)
    checks = {r.check for r in result.risks}
    assert "shell_wrapper" in checks
    assert "remote_executable" in checks  # npx
    assert "wildcard_tools" in checks
    assert "env_secret_inline" in checks
    assert result.overall_severity in ("high", "critical")


def test_audit_mcp_empty_config(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text("{}", encoding="utf-8")
    infos = find_mcp_configs(tmp_path)
    assert len(infos) == 1
    result = audit_mcp(infos[0], llm=None)
    checks = {r.check for r in result.risks}
    assert "empty_config" in checks


def test_find_mcp_configs_python_server(tmp_path):
    py = tmp_path / "server.py"
    py.write_text(
        "from mcp.server import Server\n"
        "srv = Server(name='myserver')\n",
        encoding="utf-8",
    )
    infos = find_mcp_configs(tmp_path)
    py_infos = [i for i in infos if i.kind == "python_server"]
    assert len(py_infos) == 1
    assert py_infos[0].servers[0].name == "myserver"


def test_audit_mcp_dangerous_arg(tmp_path):
    """server 命令含 -c 危险参数应被识别。"""
    payload = {
        "mcpServers": {
            "x": {"command": "/usr/bin/runner", "args": ["-c", "rm -rf /"]},
        }
    }
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps(payload), encoding="utf-8")
    infos = find_mcp_configs(tmp_path)
    result = audit_mcp(infos[0], llm=None)
    checks = {r.check for r in result.risks}
    assert "dangerous_arg" in checks
