"""Tests for /explain capability without real LLM calls."""
from io import StringIO

import pytest
from rich.console import Console as RichConsole

from poker.capabilities import explain as explain_mod
from poker.capabilities.explain import (
    build_explain_prompt,
    compute_finding_id,
    explain_finding,
)
from poker.models import Finding, Severity


# ---------- helpers ----------

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


# ---------- compute_finding_id ----------

def test_compute_finding_id_is_stable_and_8_chars() -> None:
    f = _sample_finding()
    a = compute_finding_id(f)
    b = compute_finding_id(dict(f))
    assert a == b
    assert len(a) == 8


def test_compute_finding_id_changes_with_path() -> None:
    f1 = _sample_finding(path="a.py")
    f2 = _sample_finding(path="b.py")
    assert compute_finding_id(f1) != compute_finding_id(f2)


def test_compute_finding_id_changes_with_line() -> None:
    f1 = _sample_finding(line=1)
    f2 = _sample_finding(line=2)
    assert compute_finding_id(f1) != compute_finding_id(f2)


def test_compute_finding_id_accepts_finding_object() -> None:
    f = Finding(
        rule_id="r", title="t", severity=Severity.HIGH, category="c",
        path="p.py", line=1, evidence="e", recommendation="rec",
    )
    fid_obj = compute_finding_id(f)
    fid_dict = compute_finding_id(f.to_dict())
    assert fid_obj == fid_dict


def test_compute_finding_id_rejects_unsupported_types() -> None:
    with pytest.raises(TypeError):
        compute_finding_id("not a finding")


# ---------- build_explain_prompt ----------

def test_build_explain_prompt_contains_key_fields() -> None:
    f = _sample_finding()
    p = build_explain_prompt(f)
    # 关键字段都嵌入了
    assert f["rule_id"] in p
    assert f["path"] in p
    assert str(f["line"]) in p
    assert f["evidence"] in p
    # 三段式输出结构
    assert "触发路径" in p
    assert "影响范围" in p
    assert "修复建议" in p
    # 引导用工具
    assert "read_file" in p
    assert "search_code" in p or "search_text" in p


# ---------- explain_finding：边界路径 ----------

def test_explain_finding_no_scan_warns(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: [])
    console, buf = _capture_console()
    explain_finding("abc", tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert "scan" in out.lower() or "扫描" in out


def test_explain_finding_empty_id_lists_recent(tmp_path, monkeypatch) -> None:
    findings = [_sample_finding(line=i) for i in range(1, 8)]
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: findings)
    console, buf = _capture_console()
    explain_finding("", tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert "finding-id" in out or "需要" in out
    # 表格里至少出现前 5 条之一的 ID
    assert compute_finding_id(findings[0]) in out


def test_explain_finding_id_not_found_lists_recent(tmp_path, monkeypatch) -> None:
    findings = [_sample_finding(line=i) for i in range(1, 4)]
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: findings)
    console, buf = _capture_console()
    explain_finding("zzzzzzzz", tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert "未找到" in out
    assert compute_finding_id(findings[0]) in out  # 候选表里能看到 ID


def test_explain_finding_ambiguous_lists_matches(tmp_path, monkeypatch) -> None:
    """同 1 字符前缀的两条 finding 用前缀触发多匹配分支。"""
    base = _sample_finding(path="a.py", line=1)
    target_prefix = compute_finding_id(base)[0]
    collision: dict | None = None
    for i in range(2, 2000):
        cand = _sample_finding(path="b.py", line=i)
        if compute_finding_id(cand).startswith(target_prefix):
            collision = cand
            break
    if collision is None:  # pragma: no cover - sha256 分布几乎必中
        pytest.skip("无法在 2000 次内找到 1-char prefix 冲突")

    findings = [base, collision]
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: findings)
    console, buf = _capture_console()
    explain_finding(target_prefix, tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert "匹配" in out
    assert compute_finding_id(base) in out
    assert compute_finding_id(collision) in out


def test_explain_finding_unique_match_no_llm_falls_back(tmp_path, monkeypatch) -> None:
    finding = _sample_finding()
    fid = compute_finding_id(finding)
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: [finding])

    console, buf = _capture_console()
    explain_finding(fid, tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert fid in out
    assert "未配置 LLM" in out or "通用建议" in out
    assert finding["recommendation"] in out


def test_explain_finding_unique_match_uses_id_prefix(tmp_path, monkeypatch) -> None:
    """前缀匹配（git checkout abc 风格）：任意非冲突短前缀都能命中。"""
    finding = _sample_finding()
    fid = compute_finding_id(finding)
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: [finding])

    console, buf = _capture_console()
    # 用前 3 位 prefix（仅 1 条 finding 不会冲突）
    explain_finding(fid[:3], tmp_path, llm=None, console=console)
    out = buf.getvalue()
    assert fid in out  # 完整 ID 在 header 里被打印


def test_explain_finding_llm_failure_falls_back(tmp_path, monkeypatch) -> None:
    finding = _sample_finding()
    fid = compute_finding_id(finding)
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: [finding])

    def _failing_stream(*args, **kwargs):
        raise RuntimeError("LLM down")
        yield  # 让函数变成 generator function

    monkeypatch.setattr(explain_mod, "stream_agent_long", _failing_stream)

    console, buf = _capture_console()
    explain_finding(fid, tmp_path, llm=object(), console=console)
    out = buf.getvalue()
    assert "失败" in out or "LLM down" in out
    # 退化到通用建议
    assert finding["recommendation"] in out


def test_explain_finding_unique_match_invokes_stream_agent_long(
    tmp_path, monkeypatch
) -> None:
    finding = _sample_finding()
    fid = compute_finding_id(finding)
    monkeypatch.setattr(explain_mod, "load_last_findings", lambda r: [finding])

    captured: dict = {}

    def _fake_stream(llm, prompt, session_id="default"):
        captured["llm"] = llm
        captured["prompt"] = prompt
        captured["session_id"] = session_id
        yield ("分析中", [], 1)
        yield ("……完成", [], 1)
        yield ("", [], 1)

    monkeypatch.setattr(explain_mod, "stream_agent_long", _fake_stream)

    console, buf = _capture_console()
    sentinel_llm = object()
    explain_finding(fid, tmp_path, llm=sentinel_llm, console=console)

    assert captured["llm"] is sentinel_llm
    assert finding["rule_id"] in captured["prompt"]
    assert captured["session_id"].startswith("explain-")
