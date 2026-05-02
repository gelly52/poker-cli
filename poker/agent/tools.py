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
from poker.state import append_audit_log, save_backup
from poker.ui.diff import show_diff_and_confirm
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


@tool
def write_file(path: str, content: str) -> str:
    """整文件覆写。写盘前显示 diff 等用户确认；用户拒绝则原文件不动并自动备份。

    路径必须在 project root 内，越界拒绝。父目录不存在会自动创建。
    """
    _audit("write_file", path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    if target.exists() and target.is_dir():
        return f"错误：目标是目录 {target}"

    if target.exists():
        try:
            old = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"错误：读取原文件失败 {e}"
    else:
        old = ""

    if not show_diff_and_confirm(old, content, path):
        return "用户拒绝"

    try:
        backup_path = save_backup(_project_root, target)
    except Exception as e:
        return f"错误：备份失败 {e}"

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as e:
        return f"错误：写入失败 {e}"

    _audit("write_file_applied", path=path, backup=str(backup_path))
    rel = target.relative_to(_project_root).as_posix()
    return f"已写入 {rel}（{len(content)} 字符；备份 {backup_path.name}）"


@tool
def apply_patch(path: str, diff: str) -> str:
    """应用 unified diff 到指定文件。失败 / 用户拒绝时原文件不动并自动备份。

    diff 必须是标准 unified diff（含 @@ hunk 头）。context / 删除行必须与
    原文件严格匹配；任何不匹配视为无效 diff，结构化返回错误。
    """
    _audit("apply_patch", path=path)
    target = _resolve_within_root(path)
    if target is None:
        return f"错误：路径越界 {path}"
    if not target.exists():
        return f"错误：文件不存在 {target}"
    if not target.is_file():
        return f"错误：不是文件 {target}"

    try:
        old = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"错误：读取原文件失败 {e}"

    try:
        new = _apply_unified_diff(old, diff)
    except ValueError as e:
        return f"错误：diff 应用失败 {e}"

    if new == old:
        return "（diff 应用后内容未变化）"

    if not show_diff_and_confirm(old, new, path):
        return "用户拒绝"

    try:
        backup_path = save_backup(_project_root, target)
    except Exception as e:
        return f"错误：备份失败 {e}"

    try:
        target.write_text(new, encoding="utf-8")
    except OSError as e:
        return f"错误：写入失败 {e}"

    _audit("apply_patch_applied", path=path, backup=str(backup_path))
    rel = target.relative_to(_project_root).as_posix()
    return f"已应用 patch 到 {rel}（备份 {backup_path.name}）"


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


def _apply_unified_diff(original: str, diff_text: str) -> str:
    """最小可用的 unified diff 应用器。

    支持：`---` / `+++` 头（忽略）、`@@ -L1,N1 +L2,N2 @@` hunk 头、
    `' '` context、`'-'` 删除、`'+'` 新增、`'\\'` no-newline 标记。
    任何 context / 删除不匹配抛 ValueError。
    """
    if not diff_text.strip():
        raise ValueError("diff 为空")

    keep_trailing = original.endswith("\n")
    src_lines = original.split("\n")
    if keep_trailing and src_lines and src_lines[-1] == "":
        src_lines.pop()

    diff_lines = diff_text.split("\n")
    out: list[str] = []
    i = 0  # index into src_lines
    j = 0  # index into diff_lines
    n = len(diff_lines)
    saw_hunk = False

    while j < n:
        line = diff_lines[j]

        if line.startswith("---") or line.startswith("+++"):
            j += 1
            continue

        if line.startswith("@@"):
            m = _HUNK_HEADER_RE.match(line)
            if not m:
                raise ValueError(f"不合法的 hunk 头: {line!r}")
            old_start = max(0, int(m.group(1)) - 1)  # 1-based -> 0-based
            while i < old_start:
                if i >= len(src_lines):
                    raise ValueError("hunk 起始位置超出原文件")
                out.append(src_lines[i])
                i += 1
            saw_hunk = True
            j += 1
            continue

        if not saw_hunk:
            # hunk 头前不允许出现内容行（除了 --- / +++）
            if line == "":
                j += 1
                continue
            raise ValueError(f"hunk 头之前出现意外内容: {line!r}")

        if line.startswith("\\"):  # \ No newline at end of file
            j += 1
            continue

        if line == "" and j == n - 1:
            j += 1
            continue

        if line.startswith(" "):
            text = line[1:]
            if i >= len(src_lines) or src_lines[i] != text:
                actual = src_lines[i] if i < len(src_lines) else "<EOF>"
                raise ValueError(
                    f"context 不匹配（原文件第 {i + 1} 行）：期望 {text!r}，实际 {actual!r}"
                )
            out.append(text)
            i += 1
            j += 1
            continue

        if line.startswith("-"):
            text = line[1:]
            if i >= len(src_lines) or src_lines[i] != text:
                actual = src_lines[i] if i < len(src_lines) else "<EOF>"
                raise ValueError(
                    f"删除行不匹配（原文件第 {i + 1} 行）：期望 {text!r}，实际 {actual!r}"
                )
            i += 1
            j += 1
            continue

        if line.startswith("+"):
            out.append(line[1:])
            j += 1
            continue

        raise ValueError(f"不识别的 diff 行：{line!r}")

    if not saw_hunk:
        raise ValueError("diff 中没有 @@ hunk 头")

    while i < len(src_lines):
        out.append(src_lines[i])
        i += 1

    result = "\n".join(out)
    if keep_trailing:
        result += "\n"
    return result


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
        write_file,
        apply_patch,
    ]
