"""Tests for /investigate capability — capability tools + run_investigation."""
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console as RichConsole

from poker import state
from poker.agent import tools as agent_tools
from poker.capabilities import investigate as investigate_mod
from poker.capabilities.investigate import (
    INVESTIGATE_SYSTEM_PROMPT,
    _build_user_prompt,
    run_investigation,
)


# ---------- helpers ----------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 Path.home() mock 到 tmp_path 下，避免污染真实用户目录。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture(autouse=True)
def _reset_budget():
    """每个测试前后都把调查预算清零，避免互相污染。"""
    agent_tools.set_investigation_budget(0)
    yield
    agent_tools.set_investigation_budget(0)


def _capture_console() -> tuple[RichConsole, StringIO]:
    buf = StringIO()
    return RichConsole(file=buf, force_terminal=False, width=140), buf


# ---------- 预算 ----------

def test_budget_starts_zero() -> None:
    used, total = agent_tools.investigation_tool_usage()
    assert (used, total) == (0, 0)


def test_set_budget_then_consume() -> None:
    agent_tools.set_investigation_budget(3)
    used, total = agent_tools.investigation_tool_usage()
    assert (used, total) == (0, 3)

    err = agent_tools._consume_investigation_budget("x")
    assert err is None
    assert agent_tools.investigation_tool_usage() == (1, 3)


def test_budget_exhausts_returns_error_message(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)

    # 第 1、2 次调用都消耗预算
    assert "上限" not in agent_tools.run_scan_tool.invoke({"target": ""})
    assert "上限" not in agent_tools.run_scan_tool.invoke({"target": ""})
    # 第 3 次返回上限错误
    out = agent_tools.run_scan_tool.invoke({"target": ""})
    assert "已达上限" in out


def test_capability_tool_rejected_outside_investigation_mode(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    # 预算 = 0 表示未启动调查模式
    out = agent_tools.run_scan_tool.invoke({"target": ""})
    assert "仅在 /investigate 模式下可用" in out


# ---------- capability 工具 ----------

def test_run_scan_tool_returns_finding_summary(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(5)

    target = tmp_path / "settings.env"
    target.write_text("API_KEY=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

    out = agent_tools.run_scan_tool.invoke({"target": ""})
    assert "1 条 finding" in out
    assert "generic-api-key" in out


def test_run_scan_tool_path_traversal_rejected(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    out = agent_tools.run_scan_tool.invoke({"target": "../outside"})
    assert "越界" in out or "路径不存在" in out


def test_read_findings_tool_empty(tmp_path, fake_home) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    out = agent_tools.read_findings_tool.invoke({})
    assert "没有 scan 结果" in out or "请先调" in out


def test_run_audit_tool_invalid_dimension(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    out = agent_tools.run_audit_tool.invoke({"dimension": "weird"})
    assert "tools/rag/mcp/prompt" in out


def test_run_audit_tool_tools_dimension_runs(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    # 项目里没有 LangChain @tool，应当返回"未发现"摘要
    out = agent_tools.run_audit_tool.invoke({"dimension": "tools"})
    assert "audit tools" in out


def test_run_trace_tool_bad_format(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    out = agent_tools.run_trace_tool.invoke({"target": "no-colons"})
    assert "格式" in out


def test_run_trace_tool_runs_on_sample(tmp_path) -> None:
    agent_tools.set_project_root(tmp_path)
    agent_tools.set_investigation_budget(2)
    src = tmp_path / "danger.py"
    src.write_text(
        "import subprocess\n"
        "def go(user_input):\n"
        "    cmd = 'echo ' + user_input\n"
        "    subprocess.run(cmd, shell=True)\n",
        encoding="utf-8",
    )
    out = agent_tools.run_trace_tool.invoke({"target": "danger.py:3:user_input"})
    assert "verdict=" in out


# ---------- get_investigate_tools ----------

def test_get_investigate_tools_includes_capability_tools() -> None:
    names = {t.name for t in agent_tools.get_investigate_tools()}
    assert {"run_scan_tool", "run_audit_tool", "run_trace_tool", "read_findings_tool"} <= names
    # 调查模式不暴露写操作
    assert "write_file" not in names
    assert "apply_patch" not in names


def test_get_investigate_tools_includes_read_only_tools() -> None:
    names = {t.name for t in agent_tools.get_investigate_tools()}
    assert {"list_files", "read_file", "search_text", "search_code", "git_diff", "git_status"} <= names


# ---------- prompt 模板 ----------

def test_system_prompt_mentions_budget_and_capability_tools() -> None:
    p = INVESTIGATE_SYSTEM_PROMPT
    assert "30" in p
    assert "run_scan_tool" in p
    assert "run_audit_tool" in p


def test_user_prompt_includes_topic_and_report_structure() -> None:
    p = _build_user_prompt("prompt injection 抗性")
    assert "prompt injection 抗性" in p
    assert "## 关键发现" in p
    assert "## 修复建议" in p
    assert "8 位" in p  # 强调 finding ID 引用


# ---------- save_investigation ----------

def test_save_investigation_writes_markdown(fake_home, tmp_path) -> None:
    p = state.save_investigation(tmp_path, "prompt injection", "# title\n\nbody.")
    assert p.exists()
    assert p.suffix == ".md"
    text = p.read_text(encoding="utf-8")
    assert "# title" in text
    assert "topic: prompt injection" in text  # 顶部注释里嵌了原 topic


def test_save_investigation_sanitizes_topic(fake_home, tmp_path) -> None:
    p = state.save_investigation(tmp_path, "../../etc/passwd", "x")
    # 文件名清洗后不含路径分隔符
    assert "/" not in p.name and "\\" not in p.name
    assert p.parent.name == "investigations"


# ---------- run_investigation ----------

def test_run_investigation_empty_topic_warns(tmp_path, monkeypatch) -> None:
    console, buf = _capture_console()
    out = run_investigation("", tmp_path, llm=object(), console=console)
    assert out is None
    assert "需要主题" in buf.getvalue()


def test_run_investigation_no_llm_warns(tmp_path, monkeypatch) -> None:
    console, buf = _capture_console()
    out = run_investigation("topic", tmp_path, llm=None, console=console)
    assert out is None
    assert "API key" in buf.getvalue() or "未配置 LLM" in buf.getvalue()


def test_run_investigation_persists_report_and_uses_capability_tools(
    fake_home, tmp_path, monkeypatch
) -> None:
    captured: dict = {}

    def _fake_stream(llm, prompt, session_id="default", **kwargs):
        captured["session_id"] = session_id
        captured["tools"] = kwargs.get("tools")
        captured["system_prompt"] = kwargs.get("system_prompt")
        captured["max_rounds"] = kwargs.get("max_rounds")
        captured["prompt"] = prompt
        # 验证调用时已在调查模式
        used, total = agent_tools.investigation_tool_usage()
        captured["budget_during_call"] = (used, total)
        yield ("# 安全调查：t\n\n", [], 1)
        yield ("## 关键发现\n- foo\n", [], 1)
        yield ("", [], 1)

    monkeypatch.setattr(investigate_mod, "stream_agent_long", _fake_stream)

    console, buf = _capture_console()
    path = run_investigation("test topic", tmp_path, llm=object(), console=console)

    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "# 安全调查：t" in body
    assert "## 关键发现" in body
    # 调查工具集 + system prompt 注入了
    tool_names = {t.name for t in captured["tools"]}
    assert "run_scan_tool" in tool_names
    assert "调查 Agent" in captured["system_prompt"]
    # 调用时预算就位
    assert captured["budget_during_call"] == (0, 30)
    # 调用结束预算清零
    assert agent_tools.investigation_tool_usage() == (0, 0)
    # session_id 命名规则
    assert captured["session_id"].startswith("investigate-")
    assert "落盘" in buf.getvalue()


def test_run_investigation_keyboard_interrupt_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    def _interrupt_stream(llm, prompt, session_id="default", **kwargs):
        yield ("# partial report\n\nsome content", [], 1)
        raise KeyboardInterrupt

    monkeypatch.setattr(investigate_mod, "stream_agent_long", _interrupt_stream)

    console, buf = _capture_console()
    path = run_investigation("topic", tmp_path, llm=object(), console=console)

    assert path is not None and path.exists()
    assert "partial report" in path.read_text(encoding="utf-8")
    assert "中断" in buf.getvalue()
    # 预算被关闭了
    assert agent_tools.investigation_tool_usage() == (0, 0)


def test_run_investigation_llm_exception_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    def _bad_stream(llm, prompt, session_id="default", **kwargs):
        yield ("# half report", [], 1)
        raise RuntimeError("LLM down")

    monkeypatch.setattr(investigate_mod, "stream_agent_long", _bad_stream)

    console, buf = _capture_console()
    path = run_investigation("topic", tmp_path, llm=object(), console=console)

    assert path is not None and path.exists()
    assert "half report" in path.read_text(encoding="utf-8")
    assert "异常" in buf.getvalue() or "失败" in buf.getvalue()
    assert agent_tools.investigation_tool_usage() == (0, 0)


def test_run_investigation_empty_output_does_not_persist(
    fake_home, tmp_path, monkeypatch
) -> None:
    def _empty_stream(llm, prompt, session_id="default", **kwargs):
        yield ("", [], 1)

    monkeypatch.setattr(investigate_mod, "stream_agent_long", _empty_stream)

    console, buf = _capture_console()
    path = run_investigation("topic", tmp_path, llm=object(), console=console)

    assert path is None
    assert "未生成" in buf.getvalue()
    inv_dir = state.get_state_dir(tmp_path) / "investigations"
    assert not inv_dir.exists() or not list(inv_dir.iterdir())
