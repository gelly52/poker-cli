"""Tests for poker.cli.repl helpers（不测交互输入循环）。"""
from pathlib import Path

from poker.cli.repl import _ReplState


def test_repl_state_default_cwd():
    state = _ReplState()
    assert state.cwd == Path.cwd().resolve()
    assert state.session_id == "repl"
