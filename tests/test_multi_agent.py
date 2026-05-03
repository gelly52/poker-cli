"""Tests for multi-agent collaborative investigation."""
import json
import time
from io import StringIO
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from rich.console import Console as RichConsole

from poker import state
from poker.capabilities import multi_agent as ma_mod
from poker.capabilities.multi_agent import (
    MAX_AGENTS,
    run_multi_agent_investigation,
)
from poker.capabilities.multi_agent import roles as roles_mod
from poker.capabilities.multi_agent.roles import (
    PER_AGENT_BUDGET,
    run_critic,
    run_planner,
    run_synthesizer,
)


# ---------- helpers ----------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _capture_console() -> tuple[RichConsole, StringIO]:
    buf = StringIO()
    return RichConsole(file=buf, force_terminal=False, width=140), buf


class _LLM:
    """简单 mock：按 invoke 顺序返回脚本内容。"""

    def __init__(self, *outputs):
        self._outputs = list(outputs)
        self.calls: list[str] = []

    def invoke(self, messages):
        if isinstance(messages, list) and messages:
            self.calls.append(messages[0].content)
        out = self._outputs.pop(0) if self._outputs else ""
        return AIMessage(content=out)


# ---------- run_planner ----------

def test_planner_parses_clean_json() -> None:
    payload = json.dumps(
        [
            {"id": "sq1", "goal": "g1", "scope": "s1"},
            {"id": "sq2", "goal": "g2", "scope": "s2"},
        ]
    )
    out = run_planner("topic", _LLM(payload))
    assert len(out) == 2
    assert out[0]["id"] == "sq1"
    assert out[1]["scope"] == "s2"


def test_planner_strips_markdown_fence() -> None:
    payload = (
        "```json\n"
        + json.dumps([{"id": "sq1", "goal": "g", "scope": ""}])
        + "\n```"
    )
    out = run_planner("topic", _LLM(payload))
    assert out[0]["goal"] == "g"


def test_planner_caps_subtasks() -> None:
    payload = json.dumps(
        [{"id": f"sq{i}", "goal": f"g{i}", "scope": ""} for i in range(1, 11)]
    )
    out = run_planner("topic", _LLM(payload), max_subtasks=5)
    assert len(out) == 5
    assert out[-1]["id"] == "sq5"


def test_planner_invalid_json_falls_back_to_single_subtask() -> None:
    out = run_planner("complex topic", _LLM("not json"))
    assert len(out) == 1
    assert out[0]["goal"] == "complex topic"


def test_planner_handles_llm_exception() -> None:
    class _Boom:
        def invoke(self, _):
            raise RuntimeError("down")

    out = run_planner("topic", _Boom())
    assert len(out) == 1
    assert out[0]["goal"] == "topic"


def test_planner_filters_invalid_items() -> None:
    payload = json.dumps(
        [
            {"id": "sq1", "goal": "g1"},
            "not a dict",
            {"id": "sq2"},  # 缺 goal
            {"id": "sq3", "goal": "g3", "scope": "s3"},
        ]
    )
    out = run_planner("topic", _LLM(payload))
    ids = [t["id"] for t in out]
    assert "sq1" in ids
    assert "sq3" in ids
    assert "sq2" not in ids


def test_planner_none_llm_returns_fallback() -> None:
    out = run_planner("topic", None)
    assert len(out) == 1
    assert out[0]["goal"] == "topic"


# ---------- critic / synthesizer ----------

def test_critic_returns_text() -> None:
    out = run_critic("topic", {"sq1": "report"}, _LLM("## sq1\n- 关键问题：覆盖不全"))
    assert "关键问题" in out


def test_critic_no_reports_returns_empty() -> None:
    assert run_critic("topic", {}, _LLM("ignored")) == ""


def test_critic_handles_llm_exception() -> None:
    class _Boom:
        def invoke(self, _):
            raise RuntimeError("down")

    out = run_critic("topic", {"sq1": "x"}, _Boom())
    assert "Critic 调用失败" in out


def test_synthesizer_returns_text() -> None:
    out = run_synthesizer(
        "topic",
        {"sq1": "report"},
        "critique",
        _LLM("# 多 Agent 调查：topic\n\n## 概述\n..."),
    )
    assert "多 Agent 调查" in out


def test_synthesizer_handles_llm_exception() -> None:
    class _Boom:
        def invoke(self, _):
            raise RuntimeError("down")

    out = run_synthesizer("topic", {"sq1": "x"}, "c", _Boom())
    assert "Synthesizer 调用失败" in out


# ---------- run_multi_agent_investigation 主流程 ----------

def _patch_roles(monkeypatch, *, planner=None, investigator=None, critic=None, synth=None):
    """方便地替换 4 个角色函数。"""
    if planner is not None:
        monkeypatch.setattr(ma_mod, "run_planner", planner)
    if investigator is not None:
        monkeypatch.setattr(ma_mod, "run_investigator", investigator)
    if critic is not None:
        monkeypatch.setattr(ma_mod, "run_critic", critic)
    if synth is not None:
        monkeypatch.setattr(ma_mod, "run_synthesizer", synth)


def test_multi_agent_empty_topic_warns(fake_home, tmp_path) -> None:
    console, buf = _capture_console()
    out = run_multi_agent_investigation("", tmp_path, llm=object(), console=console)
    assert out is None
    assert "需要主题" in buf.getvalue()


def test_multi_agent_no_llm_warns(fake_home, tmp_path) -> None:
    console, buf = _capture_console()
    out = run_multi_agent_investigation("t", tmp_path, llm=None, console=console)
    assert out is None
    assert "API key" in buf.getvalue() or "未配置 LLM" in buf.getvalue()


def test_multi_agent_full_flow_persists(fake_home, tmp_path, monkeypatch) -> None:
    sub_tasks = [
        {"id": "sq1", "goal": "g1", "scope": "s1"},
        {"id": "sq2", "goal": "g2", "scope": "s2"},
        {"id": "sq3", "goal": "g3", "scope": "s3"},
    ]
    invocations: dict = {}

    def _fake_inv(topic, sub, root, llm):
        invocations[sub["id"]] = True
        return f"## 关键发现\n- finding `aaa{sub['id'][-1]}` ...\n", None

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: sub_tasks,
        investigator=_fake_inv,
        critic=lambda topic, reports, llm: "## sq1\n- 关键问题：x",
        synth=lambda topic, reports, critique, llm: "# 多 Agent 调查：t\n\n## 概述\n完成",
    )

    console, buf = _capture_console()
    path = run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)

    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "# 多 Agent 调查：t" in body
    # 附录里有所有 sub_task 的产出
    for sid in ["sq1", "sq2", "sq3"]:
        assert sid in body
    # 3 个 investigator 全部被调用
    assert set(invocations) == {"sq1", "sq2", "sq3"}
    # 落盘到 multi_agent_runs/
    assert path.parent.name == "multi_agent_runs"


def test_multi_agent_partial_failure_marks_failed(
    fake_home, tmp_path, monkeypatch
) -> None:
    sub_tasks = [
        {"id": "sq1", "goal": "g1", "scope": ""},
        {"id": "sq2", "goal": "g2", "scope": ""},
    ]

    def _fake_inv(topic, sub, root, llm):
        if sub["id"] == "sq2":
            return "", "RuntimeError: simulated"
        return "## 关键发现\n- ok\n", None

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: sub_tasks,
        investigator=_fake_inv,
        critic=lambda topic, reports, llm: "critique",
        synth=lambda topic, reports, critique, llm: "# 多 Agent 调查：t",
    )

    console, buf = _capture_console()
    path = run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)
    body = path.read_text(encoding="utf-8")
    assert "[Investigator sq2: 失败 - RuntimeError: simulated]" in body
    # critic / synth 仍然在，因为有成功的 sq1
    assert "# 多 Agent 调查：t" in body
    # console 也提示 failed
    assert "✗ sq2" in buf.getvalue()


def test_multi_agent_all_investigators_fail_critic_skipped(
    fake_home, tmp_path, monkeypatch
) -> None:
    sub_tasks = [{"id": "sq1", "goal": "g1", "scope": ""}]
    critic_called = []

    def _critic(topic, reports, llm):
        critic_called.append(True)
        return "should not appear"

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: sub_tasks,
        investigator=lambda topic, sub, root, llm: ("", "boom"),
        critic=_critic,
        synth=lambda topic, reports, critique, llm: "# final",
    )

    console, _ = _capture_console()
    path = run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)
    body = path.read_text(encoding="utf-8")
    assert critic_called == []  # critic 没有被调用
    # 报告里说所有 Investigator 失败
    assert "所有 Investigator 失败" in body


def test_multi_agent_caps_subtasks_at_max(
    fake_home, tmp_path, monkeypatch
) -> None:
    too_many = [{"id": f"sq{i}", "goal": f"g{i}", "scope": ""} for i in range(1, 10)]
    seen_ids: list[str] = []

    def _fake_inv(topic, sub, root, llm):
        seen_ids.append(sub["id"])
        return "ok", None

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: too_many,
        investigator=_fake_inv,
        critic=lambda topic, reports, llm: "c",
        synth=lambda topic, reports, critique, llm: "# done",
    )

    console, buf = _capture_console()
    run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)
    # 最多并发 MAX_AGENTS=5 个
    assert len(seen_ids) == MAX_AGENTS
    assert "超上限" in buf.getvalue()


def test_multi_agent_planner_failure_falls_back_to_single_subtask(
    fake_home, tmp_path, monkeypatch
) -> None:
    """planner 内部已有兜底；这里验证编排层不爆。"""

    def _fake_inv(topic, sub, root, llm):
        return f"reported on {sub['goal']}", None

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: [
            {"id": "sq1", "goal": topic, "scope": ""}
        ],
        investigator=_fake_inv,
        critic=lambda topic, reports, llm: "c",
        synth=lambda topic, reports, critique, llm: "# done",
    )

    console, _ = _capture_console()
    path = run_multi_agent_investigation("topic", tmp_path, llm=object(), console=console)
    assert path is not None and path.exists()


def test_multi_agent_synth_exception_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    """synthesizer 抛异常时主流程不抛栈，已完成阶段仍落盘。"""

    def _bad_synth(topic, reports, critique, llm):
        raise RuntimeError("synth boom")

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: [
            {"id": "sq1", "goal": "g1", "scope": ""}
        ],
        investigator=lambda topic, sub, root, llm: ("## 关键发现\n- f", None),
        critic=lambda topic, reports, llm: "critique here",
        synth=_bad_synth,
    )

    console, buf = _capture_console()
    path = run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    # 没有 final，是兜底拼装的"未完成"报告
    assert "未完成" in body
    assert "sq1" in body
    assert "critique here" in body
    # 控制台提示协作异常
    assert "协作异常" in buf.getvalue()


def test_multi_agent_keyboard_interrupt_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    """投资者阶段被打断 → 已完成的子任务仍写进报告。"""

    sub_tasks = [
        {"id": "sq1", "goal": "g1", "scope": ""},
        {"id": "sq2", "goal": "g2", "scope": ""},
    ]
    call_order: list[str] = []

    def _fake_inv(topic, sub, root, llm):
        call_order.append(sub["id"])
        if sub["id"] == "sq1":
            return "## 关键发现\n- partial", None
        # 第二个 worker 抛 KeyboardInterrupt 模拟用户中断
        raise KeyboardInterrupt

    synth_called = []

    def _synth(*a, **kw):
        synth_called.append(True)
        return "# final"

    _patch_roles(
        monkeypatch,
        planner=lambda topic, llm, max_subtasks=5: sub_tasks,
        investigator=_fake_inv,
        critic=lambda topic, reports, llm: "c",
        synth=_synth,
    )

    console, buf = _capture_console()
    path = run_multi_agent_investigation("t", tmp_path, llm=object(), console=console)

    # 落盘了
    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "未完成" in body
    # synth 没被调用
    assert synth_called == []
    assert "中断" in buf.getvalue()
