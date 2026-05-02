"""Tests for poker.capabilities.audit.mcp_schema."""
import json
from pathlib import Path

import pytest
from rich.console import Console

from poker.capabilities.audit.mcp_schema import (
    audit_schema,
    format_report,
    run_mcp_schema_audit,
)


# ---------- helper: 构造单个 tool ----------

def _tool(name="tool", description="A tool that does something useful for users.", **schema_kw):
    """生成一个最小合法 tool dict；schema_kw 透传到 inputSchema。"""
    schema = {"type": "object", "additionalProperties": False, "properties": {}, **schema_kw}
    return {"name": name, "description": description, "inputSchema": schema}


def _audit_one(tool_dict) -> dict:
    """对单个 tool 跑 audit，返回 {rule_id: severity} 命中映射。"""
    reports = audit_schema({"tools": [tool_dict]})
    assert len(reports) == 1
    return {rid: sev for rid, sev, _ in reports[0].risks}


# =========================================================================
# high 规则
# =========================================================================

def test_shell_param_hit():
    t = _tool(properties={"command": {"type": "string", "maxLength": 100}})
    hits = _audit_one(t)
    assert hits.get("shell_param") == "high"


def test_shell_param_miss():
    t = _tool(properties={"status": {"type": "string", "maxLength": 16}})
    assert "shell_param" not in _audit_one(t)


def test_path_unconstrained_hit():
    t = _tool(properties={"path": {"type": "string", "maxLength": 200}})
    assert _audit_one(t).get("path_unconstrained") == "high"


def test_path_constrained_miss():
    """有 pattern 约束就不算无约束。"""
    t = _tool(properties={"path": {
        "type": "string", "pattern": "^/safe/.*", "maxLength": 200,
    }})
    assert "path_unconstrained" not in _audit_one(t)


def test_path_with_enum_miss():
    t = _tool(properties={"file": {
        "type": "string", "enum": ["a.txt", "b.txt"], "maxLength": 16,
    }})
    assert "path_unconstrained" not in _audit_one(t)


def test_url_unconstrained_hit():
    t = _tool(properties={"url": {"type": "string", "maxLength": 200}})
    assert _audit_one(t).get("url_unconstrained") == "high"


def test_url_with_pattern_miss():
    t = _tool(properties={"endpoint": {
        "type": "string", "pattern": r"^https://api\.internal\.local/", "maxLength": 200,
    }})
    assert "url_unconstrained" not in _audit_one(t)


def test_structured_string_hit():
    t = _tool(properties={"config": {"type": "string", "maxLength": 1000}})
    assert _audit_one(t).get("structured_string") == "high"


def test_structured_string_with_enum_miss():
    t = _tool(properties={"config": {
        "type": "string", "enum": ["mode_a", "mode_b"], "maxLength": 16,
    }})
    assert "structured_string" not in _audit_one(t)


# =========================================================================
# medium 规则
# =========================================================================

def test_additional_properties_open_when_missing():
    """additionalProperties 字段缺失 → 命中（默认值是 true）。"""
    t = {
        "name": "x",
        "description": "Tool with no additional_properties set explicitly.",
        "inputSchema": {"type": "object", "properties": {"x": {"type": "string", "maxLength": 8}}},
    }
    assert _audit_one(t).get("additional_properties_open") == "medium"


def test_additional_properties_open_when_true():
    t = _tool(additionalProperties=True, properties={"x": {"type": "string", "maxLength": 8}})
    assert _audit_one(t).get("additional_properties_open") == "medium"


def test_additional_properties_closed_miss():
    t = _tool(additionalProperties=False, properties={"x": {"type": "string", "maxLength": 8}})
    assert "additional_properties_open" not in _audit_one(t)


def test_too_many_required_hit():
    t = _tool(
        properties={f"p{i}": {"type": "string", "maxLength": 8} for i in range(8)},
        required=[f"p{i}" for i in range(6)],
    )
    assert _audit_one(t).get("too_many_required") == "medium"


def test_too_many_required_boundary():
    """5 个 required 不命中（>5 才算）。"""
    t = _tool(
        properties={f"p{i}": {"type": "string", "maxLength": 8} for i in range(5)},
        required=[f"p{i}" for i in range(5)],
    )
    assert "too_many_required" not in _audit_one(t)


def test_short_description_hit():
    t = _tool(description="Short")
    assert _audit_one(t).get("short_description") == "medium"


def test_short_description_empty():
    t = _tool(description="")
    assert _audit_one(t).get("short_description") == "medium"


def test_short_description_long_miss():
    t = _tool(description="A reasonably descriptive tool that does specific things.")
    assert "short_description" not in _audit_one(t)


def test_no_max_length_hit():
    t = _tool(properties={"q": {"type": "string"}})
    # q 是 string 但没 maxLength
    assert _audit_one(t).get("no_max_length") == "medium"


def test_no_max_length_with_max_miss():
    t = _tool(properties={"q": {"type": "string", "maxLength": 100}})
    assert "no_max_length" not in _audit_one(t)


# =========================================================================
# low 规则
# =========================================================================

def test_internal_word_hit():
    t = _tool(description="An admin debug tool for internal use only by SRE.")
    assert _audit_one(t).get("internal_word") == "low"


def test_internal_word_miss():
    t = _tool(description="A user-facing tool for finding documents in the workspace.")
    assert "internal_word" not in _audit_one(t)


def test_destructive_name_hit():
    t = _tool(name="delete_user", description="Delete a user from the system permanently.")
    assert _audit_one(t).get("destructive_name") == "low"


def test_destructive_name_miss():
    t = _tool(name="list_users", description="List users by workspace membership status.")
    assert "destructive_name" not in _audit_one(t)


# =========================================================================
# 整体行为
# =========================================================================

def test_overall_severity_takes_highest():
    t = _tool(
        name="delete_things",  # low
        description="Short",     # medium
        properties={"command": {"type": "string"}},  # high (shell_param) + medium (no_max_length)
    )
    reports = audit_schema({"tools": [t]})
    assert reports[0].overall == "high"


def test_clean_tool_is_safe():
    t = _tool(
        name="list_things",
        description="A safe descriptive tool that lists items by status filter.",
        additionalProperties=False,
        properties={"status": {
            "type": "string",
            "enum": ["a", "b"],
            "maxLength": 16,
        }},
        required=["status"],
    )
    reports = audit_schema({"tools": [t]})
    assert reports[0].overall == "safe"
    assert not reports[0].risks


def test_audit_schema_accepts_bare_array():
    arr = [_tool(name="a", description="A safe descriptive tool that lists.",
                 additionalProperties=False,
                 properties={"x": {"type": "string", "enum": ["1"], "maxLength": 4}},
                 required=["x"])]
    reports = audit_schema(arr)
    assert len(reports) == 1


def test_audit_schema_accepts_tools_dict():
    reports = audit_schema({"tools": [_tool()]})
    assert len(reports) == 1


def test_audit_schema_handles_garbage():
    """非 dict / list → 空报告。"""
    assert audit_schema("not a dict") == []
    assert audit_schema(None) == []
    assert audit_schema({"tools": "wrong"}) == []


def test_audit_schema_skips_non_dict_tool_entries():
    reports = audit_schema({"tools": [_tool(), "not a tool", 42]})
    assert len(reports) == 1


# =========================================================================
# format_report
# =========================================================================

def test_format_report_includes_summary():
    reports = audit_schema({"tools": [
        _tool(name="bad", properties={"command": {"type": "string"}}),
        _tool(name="ok",
              description="A safe descriptive tool that lists.",
              properties={"x": {"type": "string", "enum": ["1"], "maxLength": 4}},
              required=["x"]),
    ]})
    text = format_report(reports)
    assert "[high]" in text
    assert "[safe]" in text
    assert "high 1" in text
    assert "safe 1" in text


# =========================================================================
# run_mcp_schema_audit 文件层
# =========================================================================

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_run_handles_missing_file(fake_home, tmp_path, capsys):
    console = Console(force_terminal=False, width=200)
    run_mcp_schema_audit(tmp_path / "nope.json", tmp_path, console)
    captured = capsys.readouterr()
    assert "schema 文件不存在" in captured.out


def test_run_handles_invalid_json_with_line(fake_home, tmp_path, capsys):
    f = tmp_path / "bad.json"
    f.write_text("{\n  \"tools\": [\n    invalid\n  ]\n}", encoding="utf-8")
    console = Console(force_terminal=False, width=200)
    run_mcp_schema_audit(f, tmp_path, console)
    captured = capsys.readouterr()
    assert "非法 JSON" in captured.out
    assert "行" in captured.out  # 行号有出现


def test_run_handles_empty_tools_list(fake_home, tmp_path, capsys):
    f = tmp_path / "empty.json"
    f.write_text(json.dumps({"tools": []}), encoding="utf-8")
    console = Console(force_terminal=False, width=200)
    run_mcp_schema_audit(f, tmp_path, console)
    captured = capsys.readouterr()
    assert "未在 schema 中发现 tool" in captured.out


def test_run_saves_audit(fake_home, tmp_path):
    f = tmp_path / "ok.json"
    f.write_text(json.dumps({"tools": [_tool()]}), encoding="utf-8")
    console = Console(force_terminal=False, width=200)
    run_mcp_schema_audit(f, tmp_path, console)
    # state dir 在 fake_home 下
    audits = list((fake_home / ".poker" / "state").glob("*/audits/mcp_schema_*.json"))
    assert audits


# =========================================================================
# e2e 样例（来自 tests/e2e/sample_project/mcp_schema_demo/）
# =========================================================================

_DEMO_DIR = Path(__file__).parent / "e2e" / "sample_project" / "mcp_schema_demo"


def test_safe_schema_has_zero_high():
    data = json.loads((_DEMO_DIR / "safe_schema.json").read_text(encoding="utf-8"))
    reports = audit_schema(data)
    high_count = sum(1 for r in reports if r.overall == "high")
    assert high_count == 0, f"safe_schema 期望 0 high，实得 {high_count}"


def test_bad_schema_has_at_least_3_high():
    data = json.loads((_DEMO_DIR / "bad_schema.json").read_text(encoding="utf-8"))
    reports = audit_schema(data)
    high_count = sum(1 for r in reports if r.overall == "high")
    assert high_count >= 3, f"bad_schema 期望 ≥3 high，实得 {high_count}"
    high_tools = [r.name for r in reports if r.overall == "high"]
    assert "execute_command" in high_tools
    assert "read_file" in high_tools
    assert "fetch_url" in high_tools
