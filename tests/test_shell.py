"""Tests for poker.shell：locate_bash + cwd 文件解析 + fallback 行为。"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from poker import shell as shell_mod
from poker.shell import _read_cwd_file, locate_bash, run_shell


def test_locate_bash_uses_env_override(tmp_path, monkeypatch):
    fake_bash = tmp_path / "my_bash"
    fake_bash.write_text("#!/bin/sh\n")
    monkeypatch.setenv("POKER_SHELL", str(fake_bash))
    assert locate_bash() == str(fake_bash)


def test_locate_bash_ignores_env_override_if_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("POKER_SHELL", str(tmp_path / "does_not_exist"))
    locate_bash()  # 不应抛异常；返回值依赖系统真实 PATH，这里不强校验内容


@pytest.mark.skipif(os.name != "nt", reason="git-bash 注册表查询仅 Windows 相关")
def test_locate_bash_finds_via_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("POKER_SHELL", raising=False)
    fake_bash = tmp_path / "bin" / "bash.exe"
    fake_bash.parent.mkdir()
    fake_bash.write_text("")
    with patch.object(shell_mod, "_iter_git_install_paths", return_value=iter([str(tmp_path)])):
        assert locate_bash() == str(fake_bash)


@pytest.mark.skipif(os.name != "nt", reason="WSL bash 排除仅 Windows 相关")
def test_locate_bash_skips_wsl_bash_on_windows(monkeypatch):
    monkeypatch.delenv("POKER_SHELL", raising=False)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.setenv(var, "C:\\__nonexistent__")
    with patch.object(shell_mod, "_iter_git_install_paths", return_value=iter([])), \
         patch.object(shell_mod.shutil, "which", return_value="C:\\Windows\\System32\\bash.exe"):
        assert locate_bash() is None


@pytest.mark.skipif(os.name != "nt", reason="WindowsApps bash 排除仅 Windows 相关")
def test_locate_bash_skips_windowsapps_bash(monkeypatch):
    monkeypatch.delenv("POKER_SHELL", raising=False)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        monkeypatch.setenv(var, "C:\\__nonexistent__")
    fake_path = "C:\\Users\\me\\AppData\\Local\\Microsoft\\WindowsApps\\bash.exe"
    with patch.object(shell_mod, "_iter_git_install_paths", return_value=iter([])), \
         patch.object(shell_mod.shutil, "which", return_value=fake_path):
        assert locate_bash() is None


def test_read_cwd_file_returns_existing_dir(tmp_path):
    cwd_file = tmp_path / "pwd.txt"
    cwd_file.write_text(str(tmp_path))
    assert _read_cwd_file(str(cwd_file)) == tmp_path.resolve()


def test_read_cwd_file_empty_returns_none(tmp_path):
    cwd_file = tmp_path / "pwd.txt"
    cwd_file.write_text("")
    assert _read_cwd_file(str(cwd_file)) is None


def test_read_cwd_file_invalid_path_returns_none(tmp_path):
    cwd_file = tmp_path / "pwd.txt"
    cwd_file.write_text(str(tmp_path / "definitely_does_not_exist_xyz"))
    assert _read_cwd_file(str(cwd_file)) is None


def test_read_cwd_file_missing_file_returns_none(tmp_path):
    assert _read_cwd_file(str(tmp_path / "no_such_file")) is None


@pytest.mark.skipif(os.name != "nt", reason="POSIX→Windows 路径转换仅 Windows 相关")
def test_read_cwd_file_converts_git_bash_posix_path(tmp_path):
    win = str(tmp_path)
    drive = win[0].lower()
    rest = win[2:].replace("\\", "/")
    posix_form = f"/{drive}{rest}"
    cwd_file = tmp_path / "pwd.txt"
    cwd_file.write_text(posix_form)
    assert _read_cwd_file(str(cwd_file)) == tmp_path.resolve()


def test_run_shell_fallback_when_no_bash(tmp_path):
    with patch.object(shell_mod, "locate_bash", return_value=None):
        result = run_shell("echo hi", tmp_path)
    assert result.new_cwd is None
    assert result.returncode == 0
