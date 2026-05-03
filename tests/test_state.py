"""Tests for poker.state — 自动记忆模块。"""
import json
from pathlib import Path

import pytest

from poker import state


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """把 Path.home() mock 到 tmp_path 下，避免污染真实用户目录。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_project_hash_is_stable(tmp_path):
    h1 = state.project_hash(tmp_path)
    h2 = state.project_hash(tmp_path)
    assert h1 == h2
    assert len(h1) == 12


def test_project_hash_differs_per_path(tmp_path):
    other = tmp_path / "child"
    other.mkdir()
    assert state.project_hash(tmp_path) != state.project_hash(other)


def test_get_state_dir_creates_directory(fake_home, tmp_path):
    d = state.get_state_dir(tmp_path)
    assert d.exists()
    assert d.is_dir()
    assert d.parent.parent.parent == fake_home


def test_chat_append_and_load(fake_home, tmp_path):
    state.append_chat(tmp_path, "user", "hello")
    state.append_chat(tmp_path, "assistant", "hi back")
    history = state.load_chat(tmp_path)
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "hello"
    assert history[1]["role"] == "assistant"


def test_load_chat_when_empty_returns_empty_list(fake_home, tmp_path):
    assert state.load_chat(tmp_path) == []


def test_save_and_load_findings(fake_home, tmp_path):
    findings = [{"rule_id": "test-1", "severity": "high", "title": "t"}]
    state.save_findings(tmp_path, findings)
    loaded = state.load_last_findings(tmp_path)
    assert loaded == findings


def test_set_triage_rejects_invalid_state(fake_home, tmp_path):
    with pytest.raises(ValueError):
        state.set_triage(tmp_path, "f1", "invalid")


def test_set_triage_persists_and_loads(fake_home, tmp_path):
    state.set_triage(tmp_path, "f1", "ignored")
    state.set_triage(tmp_path, "f2", "fixed")
    triages = state.load_triages(tmp_path)
    assert triages["f1"]["state"] == "ignored"
    assert triages["f2"]["state"] == "fixed"


def test_audit_log_appends_jsonl(fake_home, tmp_path):
    state.append_audit_log(tmp_path, {"type": "shell", "input": "!ls"})
    state.append_audit_log(tmp_path, {"type": "command", "input": "/scan"})
    log = (state.get_state_dir(tmp_path) / "audit.jsonl").read_text(encoding="utf-8")
    assert "shell" in log
    assert "command" in log
    assert log.count("\n") == 2


def test_save_audit_writes_dimension_file(fake_home, tmp_path):
    p = state.save_audit(tmp_path, "tools", "search_files", {"verdict": "high"})
    assert p.exists()
    assert "tools_search_files_" in p.name


# ---------- /resume 持续性回归测试 ----------

def _write_legacy_jsonl(tmp_path: Path, records: list[dict]) -> None:
    """直接写没有 session_id 字段的旧格式 chat_history.jsonl。"""
    p = state.get_state_dir(tmp_path) / "chat_history.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def test_resume_continuation_attaches_to_original_session(fake_home, tmp_path):
    """/resume 选旧 session 后追加的对话应归属原 session，
    不会因为时间间隔超过 30 分钟而被切到新窗口。"""
    _write_legacy_jsonl(
        tmp_path,
        [
            {"ts": "2026-05-01T10:00:00+00:00", "role": "user", "content": "old q"},
            {"ts": "2026-05-01T10:00:05+00:00", "role": "assistant", "content": "old a"},
        ],
    )

    sessions = state.load_chat_sessions(tmp_path)
    assert len(sessions) == 1
    resumed_id = sessions[0]["id"]

    # 用户在恢复的 session 里追加（时间相隔几小时，跨过 30 min gap）
    state.append_chat(tmp_path, "user", "new q", session_id=resumed_id)
    state.append_chat(tmp_path, "assistant", "new a", session_id=resumed_id)

    sessions = state.load_chat_sessions(tmp_path)
    assert len(sessions) == 1, "追加内容应当合并到原 session 而不是切出新窗口"
    contents = [m["content"] for m in sessions[0]["messages"]]
    assert contents == ["old q", "old a", "new q", "new a"]


def test_distinct_session_ids_yield_separate_windows(fake_home, tmp_path):
    """不同 session_id 即使时间相邻也分两个窗口（每个进程一个 session）。"""
    state.append_chat(tmp_path, "user", "p1 msg", session_id="session-a")
    state.append_chat(tmp_path, "assistant", "p1 reply", session_id="session-a")
    state.append_chat(tmp_path, "user", "p2 msg", session_id="session-b")

    sessions = state.load_chat_sessions(tmp_path)
    assert len(sessions) == 2
    assert {s["id"] for s in sessions} == {"session-a", "session-b"}


def test_legacy_records_still_split_by_gap(fake_home, tmp_path):
    """无 session_id 字段的旧 record 保持原逻辑（按 30 分钟 gap 切）。"""
    _write_legacy_jsonl(
        tmp_path,
        [
            {"ts": "2026-05-01T10:00:00+00:00", "role": "user", "content": "a"},
            {"ts": "2026-05-01T10:00:30+00:00", "role": "assistant", "content": "b"},
            {"ts": "2026-05-01T15:00:00+00:00", "role": "user", "content": "c"},
        ],
    )
    sessions = state.load_chat_sessions(tmp_path)
    assert len(sessions) == 2  # 5 小时 gap → 切 2 个窗口
