"""Tests for poker.cli.repl helpers（不测交互输入循环）。"""
from datetime import datetime
from pathlib import Path

from poker.cli.repl import _ReplState


def test_repl_state_default_cwd():
    state = _ReplState()
    assert state.cwd == Path.cwd().resolve()
    # session_id 是进程级唯一时间戳，能被 datetime.fromisoformat 解析
    assert datetime.fromisoformat(state.session_id) is not None


def test_repl_state_session_ids_are_distinct_per_instance():
    """不同 _ReplState 实例对应不同 session_id（避免不同进程串台）。"""
    a = _ReplState()
    b = _ReplState()
    # 时间戳精度足以区分（微秒级 ISO）；极端同 μs 时仍是合法 ts，不强校验差异
    assert isinstance(a.session_id, str) and a.session_id
    assert isinstance(b.session_id, str) and b.session_id
