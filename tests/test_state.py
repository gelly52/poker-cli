"""Tests for poker.state — 自动记忆模块。"""
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
