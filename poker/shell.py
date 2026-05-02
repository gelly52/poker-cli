"""Shell 执行抽象：bash 优先 + 临时文件持久化 cwd。

参考 Anthropic claude-code 的 Shell.ts 设计，但极简化：只支持 bash，
通过 POKER_SHELL 环境变量手动 override，不做父 shell 检测。
"""
import os
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Windows 上 PATH 里能找到的 bash，但实际是 WSL/Store 入口而非真正的 git-bash
_WSL_BASH_HINTS = ("system32", "windowsapps")


@dataclass
class ShellResult:
    stdout: str
    stderr: str
    returncode: int
    new_cwd: Path | None  # bash 模式跑完命令的真实 pwd；fallback 模式恒为 None


def locate_bash() -> str | None:
    """按优先级找 bash：

    1. 环境变量 POKER_SHELL（必须是已存在的可执行文件）
    2. Windows: 注册表 GitForWindows InstallPath → ProgramFiles 候选
    3. PATH 上的 bash；Windows 下排除 WSL/Store 入口
    """
    override = os.environ.get("POKER_SHELL")
    if override and Path(override).is_file():
        return override

    if os.name == "nt":
        win_bash = _locate_bash_windows()
        if win_bash:
            return win_bash

    bash = shutil.which("bash")
    if bash and os.name == "nt" and any(h in bash.lower() for h in _WSL_BASH_HINTS):
        return None
    return bash


def _locate_bash_windows() -> str | None:
    """按注册表 → ProgramFiles 候选目录顺序找 git-bash。"""
    for install_path in _iter_git_install_paths():
        for sub in ("bin/bash.exe", "usr/bin/bash.exe"):
            candidate = Path(install_path) / sub
            if candidate.is_file():
                return str(candidate)

    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(env_var)
        if not base:
            continue
        for sub in ("Git/bin/bash.exe", "Git/usr/bin/bash.exe"):
            candidate = Path(base) / sub
            if candidate.is_file():
                return str(candidate)
    return None


def _iter_git_install_paths() -> Iterator[str]:
    """从 Git for Windows 注册表项读 InstallPath（HKLM / HKCU / WOW6432Node）。"""
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return
    for hive, sub in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GitForWindows"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\GitForWindows"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GitForWindows"),
    ):
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, _ = winreg.QueryValueEx(key, "InstallPath")
        except OSError:
            continue
        if value:
            yield value


def run_shell(cmd: str, cwd: Path, timeout: int = 60) -> ShellResult:
    """执行 cmd。bash 可用时通过临时文件持久化 cwd，否则走系统 shell。

    Raises subprocess.TimeoutExpired 让调用方处理。
    """
    bash = locate_bash()
    if not bash:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return ShellResult(result.stdout or "", result.stderr or "", result.returncode, None)

    # 临时文件接 pwd -P，主进程读出来 → 不污染 stdout
    fd, cwd_file = tempfile.mkstemp(prefix="poker_pwd_", suffix=".txt")
    os.close(fd)
    # git-bash 接受 forward-slash 风格的 Windows 路径（D:/foo），且反斜杠在 bash 里有歧义
    cwd_for_bash = str(cwd).replace("\\", "/")
    cwd_file_for_bash = cwd_file.replace("\\", "/")

    full_cmd = (
        f"cd {shlex.quote(cwd_for_bash)} && {{ {cmd}; }}; "
        f"__rc=$?; pwd -P > {shlex.quote(cwd_file_for_bash)}; exit $__rc"
    )

    try:
        # 显式 [bash, '-c', cmd]：避免 Windows 下 shell=True 走 cmd 风格的 /c 触发 bash 把 /c 当脚本
        result = subprocess.run(
            [bash, "-c", full_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        new_cwd = _read_cwd_file(cwd_file)
    finally:
        try:
            os.unlink(cwd_file)
        except OSError:
            pass

    return ShellResult(result.stdout or "", result.stderr or "", result.returncode, new_cwd)


def _read_cwd_file(path: str) -> Path | None:
    """读 bash 写的 pwd -P 输出，做 POSIX→Windows 路径转换并校验存在。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if not raw:
        return None

    # git-bash 的 pwd -P 输出 /d/foo —— 转回 D:\foo
    if os.name == "nt" and len(raw) >= 3 and raw[0] == "/" and raw[2] == "/":
        drive = raw[1].upper()
        rest = raw[3:].replace("/", "\\")
        raw = f"{drive}:\\{rest}"

    try:
        p = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    return p if p.is_dir() else None
