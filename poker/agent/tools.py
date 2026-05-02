"""Agent 工具注册。

工具暴露给 LLM 用于读取项目内容（list / read / search / git）。所有路径敏感工具
都受模块级 _project_root 约束，越界访问被拒绝。

REPL 启动 / !cd 后调用 set_project_root() 同步当前目标项目目录。
工具调用都会写入 .poker/state/<hash>/audit.jsonl 留可审计日志。
"""
import re
import subprocess
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from poker.capabilities.scan.engine import scan_path
from poker.state import append_audit_log
from poker.workspace import iter_text_files

# 模块级状态：当前 project root；REPL 启动 / !cd 后调用 set_project_root() 更新
_project_root: Path = Path.cwd().resolve()

_MAX_FILE_BYTES = 200 * 1024  # 单文件 read 上限 200KB
_MAX_SEARCH_HITS = 100
_MAX_LIST_FILES = 200
_CODE_EXTS = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx",
    ".java", ".go", ".rs", ".rb", ".cpp", ".cc", ".c", ".h", ".hpp",
}


def set_project_root(path: Path) -> None:
    """更新当前 project root；REPL 在启动 / !cd 后调用。"""
    global _project_root
    _project_root = path.resolve()


def get_project_root() -> Path:
    return _project_root


def _resolve_within_root(path_str: str) -> Path | None:
    """解析路径到 project_root 内部；越界返回 None。空字符串返回 project_root。"""
    if not path_str:
        return _project_root
    p = Path(path_str).expanduser()
    abs_p = p.resolve() if p.is_absolute() else (_project_root / p).resolve()
    try:
        abs_p.relative_to(_project_root)
    except ValueError:
        return None
    return abs_p


def _audit(name: str, **kwargs: Any) -> None:
    """记录工具调用到审计日志；失败静默不影响工具本身。"""
    try:
        append_audit_log(_project_root, {"type": "tool", "name": name, **kwargs})
    except Exception:
        pass


@tool
def list_files(path: str = "") -> str:
    """列出 project root 内（或指定子目录下）的文本文件路径。尊重 .gitignore 风格的跳过规则。"""
    _audit("list_files", path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    if not target.exists():
        return f"错误：路径不存在 {target}"
    if not target.is_dir():
        return f"错误：不是目录 {target}"
    files = list(iter_text_files(target))
    if not files:
        return "（无匹配文件）"
    rels = sorted(f.relative_to(_project_root).as_posix() for f in files)
    if len(rels) > _MAX_LIST_FILES:
        rels = rels[:_MAX_LIST_FILES] + [f"... 还有 {len(files) - _MAX_LIST_FILES} 个文件"]
    return "\n".join(rels)


@tool
def read_file(path: str) -> str:
    """读取 project root 内的文件内容。最大 200KB，超出截断并提示。"""
    _audit("read_file", path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    if not target.exists():
        return f"错误：文件不存在 {target}"
    if not target.is_file():
        return f"错误：不是文件 {target}"
    try:
        data = target.read_bytes()
    except OSError as e:
        return f"错误：读取失败 {e}"
    truncated = len(data) > _MAX_FILE_BYTES
    head = data[:_MAX_FILE_BYTES] if truncated else data
    text = head.decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[...已截断，原文件 {len(data)} bytes，仅显示前 {_MAX_FILE_BYTES} bytes]"
    return text


@tool
def search_text(pattern: str, path: str = "") -> str:
    """正则文本搜索（项目内全部文本文件）。返回 file:line:行内容，最多 100 条。"""
    _audit("search_text", pattern=pattern, path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"错误：正则不合法 {e}"
    return _do_search(regex, target, code_only=False)


@tool
def search_code(pattern: str, path: str = "") -> str:
    """代码符号 / 模式搜索（仅 .py / .ts / .js / .go 等代码文件）。最多 100 条命中。"""
    _audit("search_code", pattern=pattern, path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"错误：正则不合法 {e}"
    return _do_search(regex, target, code_only=True)


def _do_search(regex: re.Pattern, target: Path, code_only: bool) -> str:
    """在 target（文件或目录）执行搜索；最多 _MAX_SEARCH_HITS 条命中。"""
    if target.is_file():
        files = [target]
    else:
        files = list(iter_text_files(target))
        if code_only:
            files = [f for f in files if f.suffix.lower() in _CODE_EXTS]

    hits: list[str] = []
    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(content.splitlines(), 1):
            if regex.search(line):
                rel = f.relative_to(_project_root).as_posix()
                hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                if len(hits) >= _MAX_SEARCH_HITS:
                    hits.append(f"... 命中过多，已截断到 {_MAX_SEARCH_HITS} 条")
                    return "\n".join(hits)
    return "\n".join(hits) if hits else "（无命中）"


@tool
def git_diff() -> str:
    """返回当前 project root 的 git diff 输出。"""
    _audit("git_diff")
    return _run_git("diff")


@tool
def git_status() -> str:
    """返回当前 project root 的 git status --short 输出。"""
    _audit("git_status")
    return _run_git("status --short")


def _run_git(args: str) -> str:
    """在 project_root 执行 git 命令并捕获输出。"""
    try:
        result = subprocess.run(
            f"git {args}",
            shell=True,
            cwd=_project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "错误：git 命令超时"
    except Exception as e:
        return f"错误：git 命令失败 {e}"
    out = result.stdout or ""
    if result.stderr:
        out += f"\n[stderr] {result.stderr}"
    if not out.strip():
        return "（无输出）"
    return out[:_MAX_FILE_BYTES]


@tool
def scan_project(target: str = "") -> str:
    """对 project root 内的文件或目录执行 AI 安全扫描。target 留空则扫整个 project。"""
    _audit("scan_project", target=target)
    path = _resolve_within_root(target)
    if path is None:
        return f"错误：路径越界 {target}"
    if not path.exists():
        return f"目标不存在: {path}"

    findings = scan_path(path)
    if not findings:
        return "未发现安全风险。"

    lines = [f"发现 {len(findings)} 个安全风险:\n"]
    for f in findings:
        lines.append(f"  [{f.severity.value}] {f.title} ({f.rule_id})")
        lines.append(f"    位置: {f.path}:{f.line}")
        lines.append(f"    证据: {f.evidence}")
        lines.append(f"    建议: {f.recommendation}\n")
    return "\n".join(lines)


def get_agent_tools() -> list:
    """返回 Agent 当前可用的工具列表。新增工具只需在此注册。"""
    return [
        list_files,
        read_file,
        search_text,
        search_code,
        git_diff,
        git_status,
        scan_project,
    ]
