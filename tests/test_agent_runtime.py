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


# ---------- 长链路 stream_agent_long ----------


class _ScriptedStreamAgent:
    """每次 stream() 取下一段脚本作为 chunk 输出（无 tool_call）。"""

    def __init__(self, scripts: list[str]) -> None:
        self._scripts = list(scripts)
        self.calls = 0

    def stream(self, messages):
        idx = min(self.calls, len(self._scripts) - 1)
        self.calls += 1
        yield AIMessageChunk(content=self._scripts[idx])


class _ScriptedLLM:
    """invoke() 按脚本顺序返回 reflection AIMessage。"""

    def __init__(self, reflections: list[str]) -> None:
        self._reflections = list(reflections)
        self.calls = 0

    def invoke(self, messages):
        idx = min(self.calls, len(self._reflections) - 1)
        self.calls += 1
        return AIMessage(content=self._reflections[idx])


def test_parse_reflection_valid_statuses() -> None:
    assert runtime._parse_reflection("<reflection>\nstatus: done\n</reflection>") == "done"
    assert (
        runtime._parse_reflection("<reflection>\nstatus: continue\nreason: x\n</reflection>")
        == "continue"
    )
    assert runtime._parse_reflection("<reflection>status:failed</reflection>") == "failed"


def test_parse_reflection_invalid_returns_none() -> None:
    assert runtime._parse_reflection("no tag here") is None
    assert runtime._parse_reflection("<reflection>without status</reflection>") is None
    assert runtime._parse_reflection("<reflection>status: weird</reflection>") is None
    assert runtime._parse_reflection("") is None


def test_stream_agent_long_single_round_done(monkeypatch) -> None:
    """单轮 reflection=done：行为与 stream_agent 等价，token 累积一致。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime, "create_agent", lambda llm, tools=None: _ScriptedStreamAgent(["hello"])
    )

    fake_llm = _ScriptedLLM(["<reflection>status: done</reflection>"])
    events = list(runtime.stream_agent_long(fake_llm, "hi", session_id="long-done"))
    tokens = [tok for tok, _, _ in events]
    rounds = [r for _, _, r in events]

    assert "hello" in tokens
    assert max(rounds) == 1  # 单轮
    history = runtime.get_session_history("long-done").messages
    assert [m.type for m in history] == ["human", "ai"]
    assert "hello" in history[-1].content


def test_stream_agent_long_continues_then_done(monkeypatch) -> None:
    """第 1 轮 continue → 第 2 轮 done：UI 会看到 round 切到 2。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime,
        "create_agent",
        lambda llm, tools=None: _ScriptedStreamAgent(["plan-1", "plan-2"]),
    )

    fake_llm = _ScriptedLLM(
        [
            "<reflection>\nstatus: continue\nreason: 还需信息\nnext_step: 再查一下\n</reflection>",
            "<reflection>\nstatus: done\nreason: 完成\n</reflection>",
        ]
    )
    events = list(runtime.stream_agent_long(fake_llm, "q", session_id="long-multi"))
    rounds = [r for _, _, r in events]

    assert max(rounds) == 2
    assert 1 in rounds and 2 in rounds
    # 两轮 token 都进入历史
    history = runtime.get_session_history("long-multi").messages
    final_text = history[-1].content
    assert "plan-1" in final_text and "plan-2" in final_text


def test_stream_agent_long_max_rounds_force_stop(monkeypatch) -> None:
    """reflection 一直 continue → 达 max_rounds 强制结束并提示。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime, "create_agent", lambda llm, tools=None: _ScriptedStreamAgent(["x"])
    )

    fake_llm = _ScriptedLLM(["<reflection>status: continue</reflection>"])
    events = list(
        runtime.stream_agent_long(fake_llm, "q", session_id="long-cap", max_rounds=3)
    )
    rounds = [r for _, _, r in events]
    text = "".join(tok for tok, _, _ in events)

    assert max(rounds) == 3
    assert "round 上限" in text or "上限" in text


def test_stream_agent_long_reflection_invalid_exits_gracefully(monkeypatch) -> None:
    """反思无 <reflection> 标签：当前轮结束、不抛栈、历史落盘。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime, "create_agent", lambda llm, tools=None: _ScriptedStreamAgent(["only-one"])
    )

    fake_llm = _ScriptedLLM(["totally non-reflection text"])
    events = list(runtime.stream_agent_long(fake_llm, "q", session_id="long-bad-refl"))
    rounds = [r for _, _, r in events]

    assert max(rounds) == 1  # 没进入第 2 轮
    history = runtime.get_session_history("long-bad-refl").messages
    assert "only-one" in history[-1].content


def test_stream_agent_long_reflection_llm_exception(monkeypatch) -> None:
    """反思 LLM 调用抛错：当前轮结束、不抛栈、历史落盘。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime, "create_agent", lambda llm, tools=None: _ScriptedStreamAgent(["abc"])
    )

    class _BoomLLM:
        def invoke(self, messages):
            raise RuntimeError("network down")

    events = list(runtime.stream_agent_long(_BoomLLM(), "q", session_id="long-boom"))
    history = runtime.get_session_history("long-boom").messages
    assert "abc" in history[-1].content
    assert events  # 至少 yield 过 token


def test_stream_agent_long_keyboard_interrupt_persists(monkeypatch) -> None:
    """yield 时模拟 KeyboardInterrupt：finally 仍把已完成轮的内容落盘。"""
    runtime._HISTORY_STORE.clear()
    monkeypatch.setattr(runtime, "get_agent_tools", lambda: [])
    monkeypatch.setattr(
        runtime,
        "create_agent",
        lambda llm, tools=None: _ScriptedStreamAgent(["partial-1", "partial-2"]),
    )

    # 反思总是说 continue，让循环希望进入下一轮；用户在第 2 轮 yield 时按 Ctrl+C
    fake_llm = _ScriptedLLM(["<reflection>status: continue</reflection>"])

    gen = runtime.stream_agent_long(fake_llm, "q", session_id="long-int")
    seen_rounds: set[int] = set()
    try:
        for tok, _, r in gen:
            seen_rounds.add(r)
            if r == 2 and tok == "partial-2":
                gen.throw(KeyboardInterrupt)
    except KeyboardInterrupt:
        pass

    history = runtime.get_session_history("long-int").messages
    assert [m.type for m in history] == ["human", "ai"]
    # 至少看到第 1 轮的最终文本被持久化
    assert "partial-1" in history[-1].content
