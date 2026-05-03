"""Tests for /threat-model capability without real LLM calls."""
import time
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console as RichConsole

from poker import state
from poker.capabilities import threat_model as tm_mod
from poker.capabilities.explain import compute_finding_id
from poker.capabilities.threat_model import (
    THREAT_MODEL_SYSTEM_PROMPT,
    _build_user_prompt,
    _summarize_artifacts,
    has_artifacts,
    run_threat_model,
)


# ---------- helpers ----------

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _capture_console() -> tuple[RichConsole, StringIO]:
    buf = StringIO()
    return RichConsole(file=buf, force_terminal=False, width=140), buf


def _finding(severity: str, line: int, **overrides) -> dict:
    base = {
        "rule_id": "generic-api-key",
        "title": f"f-{severity}-{line}",
        "severity": severity,
        "category": "secret",
        "path": "app.py",
        "line": line,
        "evidence": f"E{line}",
        "recommendation": "Rotate.",
    }
    base.update(overrides)
    return base


# ---------- state 聚合接口 ----------

def test_load_audit_records_empty(fake_home, tmp_path) -> None:
    assert state.load_audit_records(tmp_path) == []


def test_load_audit_records_returns_recent(fake_home, tmp_path) -> None:
    state.save_audit(tmp_path, "tools", "agent.py", {"overall_severity": "high"})
    time.sleep(0.01)
    state.save_audit(tmp_path, "prompt", "system.md", {"overall_severity": "low"})
    records = state.load_audit_records(tmp_path)
    assert len(records) == 2
    # mtime 倒序：prompt 在前
    assert records[0]["dimension"] == "prompt"
    assert records[1]["dimension"] == "tools"


def test_load_investigation_records_parses_topic(fake_home, tmp_path) -> None:
    state.save_investigation(tmp_path, "prompt injection 抗性", "# Title\n\nbody.")
    records = state.load_investigation_records(tmp_path)
    assert len(records) == 1
    assert records[0]["topic"] == "prompt injection 抗性"
    assert "# Title" in records[0]["snippet"]


def test_load_investigation_records_empty(fake_home, tmp_path) -> None:
    assert state.load_investigation_records(tmp_path) == []


def test_load_all_artifacts_aggregates(fake_home, tmp_path) -> None:
    state.save_findings(tmp_path, [_finding("high", 1)])
    state.set_triage(tmp_path, compute_finding_id(_finding("high", 1)), "accepted")
    state.save_audit(tmp_path, "tools", "x.py", {"overall_severity": "high"})
    state.save_investigation(tmp_path, "topic-a", "# X")

    arts = state.load_all_artifacts(tmp_path)
    assert len(arts["findings"]) == 1
    assert arts["triages"]
    assert len(arts["audits"]) == 1
    assert len(arts["investigations"]) == 1


def test_save_threat_model_writes(fake_home, tmp_path) -> None:
    p = state.save_threat_model(tmp_path, "# TM\n\nbody")
    assert p.exists()
    assert p.parent.name == "threat_models"
    assert "# TM" in p.read_text(encoding="utf-8")
    assert "<!-- threat-model" in p.read_text(encoding="utf-8")


# ---------- has_artifacts ----------

def test_has_artifacts_false_when_all_empty() -> None:
    assert not has_artifacts({})
    assert not has_artifacts({"findings": [], "audits": [], "investigations": []})


def test_has_artifacts_true_with_findings() -> None:
    assert has_artifacts({"findings": [_finding("low", 1)]})


def test_has_artifacts_true_with_only_investigation() -> None:
    assert has_artifacts({"investigations": [{"topic": "x", "snippet": "y"}]})


# ---------- _summarize_artifacts ----------

def test_summarize_findings_sorted_by_severity_with_id() -> None:
    f_low = _finding("low", 1)
    f_crit = _finding("critical", 2)
    arts = {"findings": [f_low, f_crit], "triages": {}}
    summary, notes = _summarize_artifacts(arts)
    # critical 必须排在 low 前面
    crit_idx = summary.find(compute_finding_id(f_crit))
    low_idx = summary.find(compute_finding_id(f_low))
    assert 0 <= crit_idx < low_idx
    assert notes == []


def test_summarize_findings_truncates_top_n() -> None:
    findings = [_finding("medium", i) for i in range(50)]
    summary, notes = _summarize_artifacts({"findings": findings})
    assert any("已按 severity 取 top" in n for n in notes)
    # 截断后 findings 只列 30 条
    listed = sum(1 for line in summary.splitlines() if line.startswith("- `"))
    assert listed == 30


def test_summarize_includes_triage_tags() -> None:
    f = _finding("high", 1)
    fid = compute_finding_id(f)
    arts = {"findings": [f], "triages": {fid: {"state": "ignored"}}}
    summary, _ = _summarize_artifacts(arts)
    assert "[triage=ignored]" in summary
    assert "## Triage 总览" in summary


def test_summarize_audits_section() -> None:
    arts = {
        "audits": [
            {"dimension": "tools", "target": "agent.py", "result": {"overall_severity": "high"}},
        ]
    }
    summary, _ = _summarize_artifacts(arts)
    assert "## Audit 记录" in summary
    assert "tools" in summary and "agent.py" in summary


def test_summarize_investigations_section() -> None:
    arts = {
        "investigations": [
            {"topic": "prompt injection", "snippet": "# title\nfindings here"}
        ]
    }
    summary, _ = _summarize_artifacts(arts)
    assert "## Investigation 记录" in summary
    assert "prompt injection" in summary


def test_summarize_handles_empty_artifacts() -> None:
    summary, notes = _summarize_artifacts({})
    assert "## Findings" in summary
    assert "（无）" in summary
    assert notes == []


# ---------- _build_user_prompt ----------

def test_user_prompt_includes_stride_and_matrix() -> None:
    p = _build_user_prompt("...", [])
    assert "Spoofing" in p
    assert "Tampering" in p
    assert "Repudiation" in p
    assert "Information Disclosure" in p
    assert "Denial of Service" in p
    assert "Elevation of Privilege" in p
    assert "风险矩阵" in p
    assert "缓解优先级" in p
    assert "目录" in p
    # 强制 6 行风险矩阵
    assert p.count("| ...") >= 6


def test_user_prompt_emits_truncation_notes_block() -> None:
    p = _build_user_prompt("素材", ["finding 共 50 条，已截取 top 30"])
    assert "素材截断说明" in p
    assert "已截取 top 30" in p


def test_system_prompt_mentions_stride_and_id_constraint() -> None:
    p = THREAT_MODEL_SYSTEM_PROMPT
    assert "STRIDE" in p
    assert "8 位" in p
    assert "全覆盖" in p


# ---------- run_threat_model ----------

def test_run_threat_model_no_llm_warns(fake_home, tmp_path) -> None:
    console, buf = _capture_console()
    out = run_threat_model(tmp_path, llm=None, console=console)
    assert out is None
    assert "API key" in buf.getvalue() or "未配置 LLM" in buf.getvalue()


def test_run_threat_model_no_artifacts_warns(fake_home, tmp_path) -> None:
    console, buf = _capture_console()
    out = run_threat_model(tmp_path, llm=object(), console=console)
    assert out is None
    out_text = buf.getvalue()
    assert "/scan" in out_text or "基础调查" in out_text


def test_run_threat_model_persists_report(fake_home, tmp_path, monkeypatch) -> None:
    state.save_findings(tmp_path, [_finding("high", 1)])

    captured: dict = {}

    def _fake_stream(llm, prompt, session_id="default", **kwargs):
        captured["session_id"] = session_id
        captured["system_prompt"] = kwargs.get("system_prompt")
        captured["max_rounds"] = kwargs.get("max_rounds")
        captured["prompt"] = prompt
        yield ("# 威胁模型：STRIDE 分析\n\n", [], 1)
        yield ("## STRIDE 分析\n", [], 1)
        yield ("", [], 1)

    monkeypatch.setattr(tm_mod, "stream_agent_long", _fake_stream)

    console, buf = _capture_console()
    path = run_threat_model(tmp_path, llm=object(), console=console)

    assert path is not None and path.exists()
    body = path.read_text(encoding="utf-8")
    assert "# 威胁模型：STRIDE 分析" in body
    # 报告路径
    assert path.parent.name == "threat_models"
    # 会话 ID 命名规则
    assert captured["session_id"].startswith("threat-model-")
    # max_rounds 为 3
    assert captured["max_rounds"] == 3
    # system prompt 含 STRIDE
    assert "STRIDE" in captured["system_prompt"]
    # finding ID 出现在 prompt 里
    assert compute_finding_id(_finding("high", 1)) in captured["prompt"]


def test_run_threat_model_truncation_logged(fake_home, tmp_path, monkeypatch) -> None:
    findings = [_finding("medium", i) for i in range(50)]
    state.save_findings(tmp_path, findings)

    def _fake_stream(llm, prompt, session_id="default", **kwargs):
        yield ("# 威胁模型\n", [], 1)

    monkeypatch.setattr(tm_mod, "stream_agent_long", _fake_stream)

    console, buf = _capture_console()
    run_threat_model(tmp_path, llm=object(), console=console)

    out_text = buf.getvalue()
    assert "素材截断" in out_text


def test_run_threat_model_keyboard_interrupt_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    state.save_findings(tmp_path, [_finding("high", 1)])

    def _interrupt_stream(llm, prompt, session_id="default", **kwargs):
        yield ("# 部分报告\n", [], 1)
        raise KeyboardInterrupt

    monkeypatch.setattr(tm_mod, "stream_agent_long", _interrupt_stream)

    console, buf = _capture_console()
    path = run_threat_model(tmp_path, llm=object(), console=console)
    assert path is not None and path.exists()
    assert "部分报告" in path.read_text(encoding="utf-8")
    assert "中断" in buf.getvalue()


def test_run_threat_model_llm_exception_persists_partial(
    fake_home, tmp_path, monkeypatch
) -> None:
    state.save_findings(tmp_path, [_finding("high", 1)])

    def _bad_stream(llm, prompt, session_id="default", **kwargs):
        yield ("# 半个报告", [], 1)
        raise RuntimeError("LLM down")

    monkeypatch.setattr(tm_mod, "stream_agent_long", _bad_stream)

    console, buf = _capture_console()
    path = run_threat_model(tmp_path, llm=object(), console=console)
    assert path is not None and path.exists()
    assert "半个报告" in path.read_text(encoding="utf-8")
    assert "异常" in buf.getvalue() or "失败" in buf.getvalue()


def test_run_threat_model_empty_output_no_persist(
    fake_home, tmp_path, monkeypatch
) -> None:
    state.save_findings(tmp_path, [_finding("high", 1)])

    def _empty_stream(llm, prompt, session_id="default", **kwargs):
        yield ("", [], 1)

    monkeypatch.setattr(tm_mod, "stream_agent_long", _empty_stream)

    console, buf = _capture_console()
    path = run_threat_model(tmp_path, llm=object(), console=console)
    assert path is None
    tm_dir = state.get_state_dir(tmp_path) / "threat_models"
    assert not tm_dir.exists() or not list(tm_dir.iterdir())
    assert "未生成" in buf.getvalue()
