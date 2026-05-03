"""Tests for /triage capability without real LLM calls."""
from io import StringIO
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from rich.console import Console as RichConsole

from poker import state
from poker.capabilities import triage as triage_mod
from poker.capabilities.explain import compute_finding_id
from poker.capabilities.triage import (
    _build_suggest_prompt,
    _parse_suggestions,
    detect_project_type,
    interactive_triage,
    suggest_triage,
)


# ---------- helpers ----------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 Path.home() mock 到 tmp_path 下，避免污染真实用户目录。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _sample_finding(**overrides) -> dict:
    base = {
        "rule_id": "generic-api-key",
        "title": "Hard-coded secret",
        "severity": "high",
        "category": "secret",
        "path": "app.py",
        "line": 5,
        "evidence": "API_KEY=abcdefghij",
        "recommendation": "Move secrets to environment variables.",
    }
    base.update(overrides)
    return base


def _capture_console() -> tuple[RichConsole, StringIO]:
    buf = StringIO()
    return RichConsole(file=buf, force_terminal=False, width=140), buf


# ---------- detect_project_type ----------

def test_detect_project_type_recognizes_python(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
    hints = detect_project_type(tmp_path)
    assert "python-project" in hints


def test_detect_project_type_recognizes_node(tmp_path) -> None:
    (tmp_path / "package.json").write_text("{}")
    hints = detect_project_type(tmp_path)
    assert "nodejs-project" in hints


def test_detect_project_type_recognizes_git_and_tests(tmp_path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "tests").mkdir()
    hints = detect_project_type(tmp_path)
    assert "git-repo" in hints
    assert "has-tests" in hints


def test_detect_project_type_empty_for_bare_dir(tmp_path) -> None:
    hints = detect_project_type(tmp_path)
    assert hints == []


# ---------- _build_suggest_prompt ----------

def test_build_suggest_prompt_includes_finding_ids() -> None:
    findings = [_sample_finding(line=1), _sample_finding(line=2)]
    p = _build_suggest_prompt(findings, ["python-project"])
    for f in findings:
        assert compute_finding_id(f) in p
    assert "python-project" in p
    assert "accepted" in p
    assert "ignored" in p
    assert "fixed" in p


# ---------- _parse_suggestions ----------

def test_parse_suggestions_plain_json() -> None:
    text = '{"abc12345": {"action": "ignored", "reason": "test fixture"}}'
    out = _parse_suggestions(text)
    assert out == {"abc12345": {"action": "ignored", "reason": "test fixture"}}


def test_parse_suggestions_markdown_fenced() -> None:
    text = '```json\n{"abc": {"action": "accepted", "reason": "real"}}\n```'
    out = _parse_suggestions(text)
    assert out["abc"]["action"] == "accepted"


def test_parse_suggestions_with_noise() -> None:
    text = (
        "Sure, here's the analysis:\n"
        '{"id1": {"action": "fixed", "reason": "patched"}}\n'
        "Hope that helps!"
    )
    out = _parse_suggestions(text)
    assert out["id1"]["action"] == "fixed"


def test_parse_suggestions_invalid_json() -> None:
    assert _parse_suggestions("not json at all") == {}
    assert _parse_suggestions("") == {}
    assert _parse_suggestions("{ broken: ") == {}


def test_parse_suggestions_filters_bad_action() -> None:
    text = (
        '{"a": {"action": "weird", "reason": "x"},'
        ' "b": {"action": "ignored", "reason": "ok"},'
        ' "c": "not a dict"}'
    )
    out = _parse_suggestions(text)
    assert "a" not in out
    assert "c" not in out
    assert out["b"]["action"] == "ignored"


# ---------- suggest_triage ----------

def test_suggest_triage_no_llm_returns_empty(tmp_path) -> None:
    assert suggest_triage([_sample_finding()], tmp_path, llm=None) == {}


def test_suggest_triage_no_findings_returns_empty(tmp_path) -> None:
    class _LLM:
        def invoke(self, msgs):  # pragma: no cover - 不应被调用
            raise AssertionError("不应被调用")

    assert suggest_triage([], tmp_path, llm=_LLM()) == {}


def test_suggest_triage_handles_llm_exception(tmp_path) -> None:
    class _LLM:
        def invoke(self, msgs):
            raise RuntimeError("network down")

    out = suggest_triage([_sample_finding()], tmp_path, llm=_LLM())
    assert out == {}


def test_suggest_triage_parses_response(tmp_path) -> None:
    finding = _sample_finding()
    fid = compute_finding_id(finding)

    class _LLM:
        def __init__(self):
            self.last_prompt = None

        def invoke(self, msgs):
            self.last_prompt = msgs[0].content
            payload = (
                "{\"" + fid + "\": "
                "{\"action\": \"accepted\", \"reason\": \"real secret\"}}"
            )
            return AIMessage(content=payload)

    llm = _LLM()
    out = suggest_triage([finding], tmp_path, llm=llm)
    assert out[fid]["action"] == "accepted"
    assert fid in llm.last_prompt


# ---------- interactive_triage ----------

def test_interactive_triage_no_scan_warns(fake_home, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [])
    console, buf = _capture_console()
    interactive_triage(tmp_path, llm=None, console=console)
    assert "扫描" in buf.getvalue() or "scan" in buf.getvalue().lower()


def test_interactive_triage_all_already_triaged(
    fake_home, tmp_path, monkeypatch
) -> None:
    finding = _sample_finding()
    fid = compute_finding_id(finding)
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [finding])
    state.set_triage(tmp_path, fid, "accepted")

    console, buf = _capture_console()
    interactive_triage(tmp_path, llm=None, console=console)
    assert "已 triage" in buf.getvalue()


def test_interactive_triage_persists_decisions(
    fake_home, tmp_path, monkeypatch
) -> None:
    f1 = _sample_finding(line=1)
    f2 = _sample_finding(line=2)
    f3 = _sample_finding(line=3)
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [f1, f2, f3])

    # mock 一个 LLM 给出有效建议（验证菜单标题里能看到）
    class _LLM:
        def invoke(self, msgs):
            payload = (
                "{"
                + ",".join(
                    f'"{compute_finding_id(f)}": '
                    f'{{"action": "ignored", "reason": "test fixture"}}'
                    for f in (f1, f2, f3)
                )
                + "}"
            )
            return AIMessage(content=payload)

    decisions = ["accepted", "ignored", "skip"]
    captured_titles: list[str] = []

    def _fake_select_one(title, items, **kwargs):
        captured_titles.append(title)
        return decisions.pop(0)

    monkeypatch.setattr(triage_mod, "select_one", _fake_select_one)

    console, buf = _capture_console()
    interactive_triage(tmp_path, llm=_LLM(), console=console)

    triages = state.load_triages(tmp_path)
    assert triages[compute_finding_id(f1)]["state"] == "accepted"
    assert triages[compute_finding_id(f2)]["state"] == "ignored"
    # f3 选 skip 不应落盘
    assert compute_finding_id(f3) not in triages
    # 菜单 title 应当含 LLM 建议标记
    assert any("LLM 建议" in t for t in captured_titles)


def test_interactive_triage_select_one_none_aborts_with_partial_save(
    fake_home, tmp_path, monkeypatch
) -> None:
    f1 = _sample_finding(line=1)
    f2 = _sample_finding(line=2)
    f3 = _sample_finding(line=3)
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [f1, f2, f3])

    decisions = ["accepted", None]  # 第 2 条返回 None 模拟 Esc/Ctrl+C

    def _fake_select_one(title, items, **kwargs):
        return decisions.pop(0)

    monkeypatch.setattr(triage_mod, "select_one", _fake_select_one)

    console, buf = _capture_console()
    interactive_triage(tmp_path, llm=None, console=console)

    triages = state.load_triages(tmp_path)
    # f1 已落盘
    assert triages[compute_finding_id(f1)]["state"] == "accepted"
    # f2 / f3 未处理
    assert compute_finding_id(f2) not in triages
    assert compute_finding_id(f3) not in triages
    assert "中断" in buf.getvalue()


def test_interactive_triage_llm_failure_continues_without_suggestions(
    fake_home, tmp_path, monkeypatch
) -> None:
    finding = _sample_finding()
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [finding])

    class _BoomLLM:
        def invoke(self, msgs):
            raise RuntimeError("LLM down")

    captured_titles: list[str] = []

    def _fake_select_one(title, items, **kwargs):
        captured_titles.append(title)
        return "accepted"

    monkeypatch.setattr(triage_mod, "select_one", _fake_select_one)

    console, buf = _capture_console()
    interactive_triage(tmp_path, llm=_BoomLLM(), console=console)

    # 落盘成功
    triages = state.load_triages(tmp_path)
    assert triages[compute_finding_id(finding)]["state"] == "accepted"
    # 退化提示出现
    assert "建议" in buf.getvalue()
    # 菜单 title 没有 LLM 建议标记
    assert all("LLM 建议" not in t for t in captured_titles)


def test_interactive_triage_skips_already_triaged(
    fake_home, tmp_path, monkeypatch
) -> None:
    f1 = _sample_finding(line=1)
    f2 = _sample_finding(line=2)
    monkeypatch.setattr(triage_mod, "load_last_findings", lambda r: [f1, f2])

    # f1 已 triage，应跳过；只对 f2 提示
    state.set_triage(tmp_path, compute_finding_id(f1), "ignored")

    seen_titles: list[str] = []

    def _fake_select_one(title, items, **kwargs):
        seen_titles.append(title)
        return "accepted"

    monkeypatch.setattr(triage_mod, "select_one", _fake_select_one)

    console, _ = _capture_console()
    interactive_triage(tmp_path, llm=None, console=console)

    assert len(seen_titles) == 1
    assert compute_finding_id(f2) in seen_titles[0]
