"""Tests for agent runtime without real LLM calls."""

from langchain_core.messages import AIMessage, AIMessageChunk

from poker.agent import runtime
from poker.agent.tools import scan_project, set_project_root


class FakeInvokeAgent:
    def invoke(self, messages):
        return AIMessage(content=f"received {len(messages)} messages")


class FakeStreamAgent:
    def stream(self, messages):
        yield AIMessageChunk(content="hel")
        yield AIMessageChunk(content="lo")


def test_run_agent_returns_text_and_updates_history(monkeypatch) -> None:
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(runtime, "create_agent", lambda llm, tools=None: FakeInvokeAgent())

    text, history = runtime.run_agent(object(), "hi", session_id="test-run")

    assert isinstance(text, str)
    assert text.startswith("received")
    assert [message.type for message in history] == ["human", "ai"]


def test_run_agent_keeps_session_history(monkeypatch) -> None:
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(runtime, "create_agent", lambda llm, tools=None: FakeInvokeAgent())

    runtime.run_agent(object(), "first", session_id="test-session")
    _, history = runtime.run_agent(object(), "second", session_id="test-session")

    assert [message.type for message in history] == ["human", "ai", "human", "ai"]


def test_stream_agent_yields_string_tokens(monkeypatch) -> None:
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(runtime, "create_agent", lambda llm, tools=None: FakeStreamAgent())

    events = list(runtime.stream_agent(object(), "hi", session_id="test-stream"))
    tokens = [token for token, _ in events]

    assert tokens == ["hel", "lo", ""]
    assert all(isinstance(token, str) for token in tokens)


def test_scan_project_reports_missing_target(tmp_path) -> None:
    set_project_root(tmp_path)
    result = scan_project.invoke({"target": "missing-target-for-test"})

    assert "目标不存在" in result


def test_scan_project_returns_findings(tmp_path) -> None:
    set_project_root(tmp_path)
    target = tmp_path / "settings.env"
    target.write_text("API_KEY=abcdefghijklmnopqrstuvwxyz123456", encoding="utf-8")

    result = scan_project.invoke({"target": str(tmp_path)})

    assert "发现 1 个安全风险" in result
    assert "generic-api-key" in result
