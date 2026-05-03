"""Agent 工具注册。

工具暴露给 LLM 用于读取项目内容（list / read / search / git）。所有路径敏感工具
都受模块级 _project_root 约束，越界访问被拒绝。

REPL 启动 / !cd 后调用 set_project_root() 同步当前目标项目目录。
工具调用都会写入 .poker/state/<hash>/audit.jsonl 留可审计日志。
"""
import re
import subprocess
import threading
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


# ---------- 调查模式（/investigate）专用 ----------
#
# /investigate 给 LLM 注入一组"重型" capability 工具（scan / audit / trace /
# read_findings），并通过 thread-local 状态控制总调用次数（默认 30），避免无限循环。
# **thread-local**：多 Agent 协作时多个 Investigator 各自起线程，每个线程有独立预算，
# 互不干扰；单线程主流程下行为跟模块级变量一致。

_budget_state = threading.local()


def set_investigation_budget(n: int) -> None:
    """启动 / 关闭调查模式（当前线程）。n>0 启动并设置可用次数；n=0 关闭。"""
    _budget_state.budget = max(0, int(n))
    _budget_state.used = 0


def investigation_tool_usage() -> tuple[int, int]:
    """返回 (已用, 总预算)；未启动调查模式时返回 (0, 0)。"""
    return getattr(_budget_state, "used", 0), getattr(_budget_state, "budget", 0)


def _consume_investigation_budget(name: str) -> str | None:
    """消耗 1 次预算；返回 None=允许调用，否则返回错误字符串给 LLM。"""
    budget = getattr(_budget_state, "budget", 0)
    if budget <= 0:
        return f"错误：{name} 仅在 /investigate 模式下可用"
    used = getattr(_budget_state, "used", 0)
    if used >= budget:
        return (
            f"错误：调查工具调用次数已达上限 ({budget})；"
            "请基于已收集信息生成最终报告，不要再调工具"
        )
    _budget_state.used = used + 1
    return None


@tool
def run_scan_tool(target: str = "") -> str:
    """跑安全扫描。target 留空 = 整个项目。返回 finding 摘要列表（含 8 位短 ID）。

    结果会同步落到 .poker/state/<hash>/last_scan.json。仅在 /investigate 模式下可用。
    """
    err = _consume_investigation_budget("run_scan_tool")
    if err:
        return err
    _audit("run_scan_tool", target=target)

    from poker.capabilities.explain import compute_finding_id
    from poker.capabilities.scan.engine import scan_path
    from poker.state import save_findings

    path = _resolve_within_root(target)
    if path is None:
        return f"错误：路径越界 {target}"
    if not path.exists():
        return f"错误：路径不存在 {path}"

    try:
        findings = scan_path(path)
    except Exception as e:
        return f"错误：扫描失败 {e}"
    save_findings(_project_root, findings)

    if not findings:
        return f"扫描完成。{path} 无 finding。"

    lines = [f"扫描完成。{path} 共 {len(findings)} 条 finding（精简列表）："]
    for f in findings[:50]:
        fid = compute_finding_id(f)
        lines.append(
            f"  [{f.severity.value:>8}] {fid} {f.rule_id} "
            f"@ {f.path}:{f.line}  -  {f.title}"
        )
    if len(findings) > 50:
        lines.append(f"  ... 还有 {len(findings) - 50} 条；用 read_findings_tool 看完整")
    return "\n".join(lines)


@tool
def run_audit_tool(dimension: str, target: str = "") -> str:
    """跑深度审计。dimension ∈ tools / rag / mcp / prompt。返回静态启发式审计摘要。

    target 当前忽略（按整项目扫）。仅在 /investigate 模式下可用，不会嵌套调用 LLM。
    """
    err = _consume_investigation_budget("run_audit_tool")
    if err:
        return err
    _audit("run_audit_tool", dimension=dimension, target=target)

    if dimension == "tools":
        return _audit_tools_summary()
    if dimension == "rag":
        return _audit_rag_summary()
    if dimension == "mcp":
        return _audit_mcp_summary()
    if dimension == "prompt":
        return _audit_prompt_summary()
    return f"错误：dimension 必须是 tools/rag/mcp/prompt，不是 {dimension!r}"


def _audit_tools_summary() -> str:
    from poker.capabilities.audit.tools import audit_tool, find_tools

    items = find_tools(_project_root)
    if not items:
        return "audit tools: 未发现 LangChain @tool / Tool() / function calling schema。"
    lines = [f"audit tools: 发现 {len(items)} 个工具："]
    for t in items[:20]:
        try:
            res = audit_tool(t, llm=None)
            risks = "; ".join(f"{r.severity}:{r.title}" for r in res.risks[:3]) or "no obvious risks"
        except Exception as e:
            risks = f"audit error: {e}"
        lines.append(f"  - {t.name} @ {t.file}:{t.line}  [{risks}]")
    if len(items) > 20:
        lines.append(f"  ... 还有 {len(items) - 20} 个工具未列出")
    return "\n".join(lines)


def _audit_rag_summary() -> str:
    from poker.capabilities.audit.rag import audit_rag, find_rag_components

    items = find_rag_components(_project_root)
    if not items:
        return "audit rag: 未发现 RAG 组件（vectorstore / retriever / loader）。"
    lines = [f"audit rag: 发现 {len(items)} 个 RAG 组件："]
    for c in items[:15]:
        try:
            res = audit_rag(c, llm=None)
            risks = "; ".join(f"{r.severity}:{r.title}" for r in res.risks[:2]) or "no obvious risks"
        except Exception as e:
            risks = f"audit error: {e}"
        lines.append(f"  - [{c.kind}] {c.method} @ {c.file}:{c.line}  [{risks}]")
    return "\n".join(lines)


def _audit_mcp_summary() -> str:
    from poker.capabilities.audit.mcp import audit_mcp, find_mcp_configs

    infos = find_mcp_configs(_project_root)
    if not infos:
        return "audit mcp: 未发现 MCP 配置或 server 实例。"
    lines = [f"audit mcp: 发现 {len(infos)} 个 MCP 配置："]
    for info in infos[:10]:
        try:
            res = audit_mcp(info, llm=None)
            risks = "; ".join(f"{r.severity}:{r.title}" for r in res.risks[:3]) or "no obvious risks"
        except Exception as e:
            risks = f"audit error: {e}"
        lines.append(
            f"  - [{info.kind}] {info.file} servers={len(info.servers)}  [{risks}]"
        )
    return "\n".join(lines)


def _audit_prompt_summary() -> str:
    from poker.capabilities.audit.prompt import audit_prompt, find_prompts

    items = find_prompts(_project_root)
    if not items:
        return "audit prompt: 未发现可疑 prompt。"
    lines = [f"audit prompt: 发现 {len(items)} 处 prompt："]
    for p in items[:15]:
        try:
            res = audit_prompt(p, llm=None)
            risks = "; ".join(f"{r.severity}:{r.title}" for r in res.risks[:3]) or "no obvious risks"
        except Exception as e:
            risks = f"audit error: {e}"
        snippet = (p.content or "").strip().splitlines()[0][:60] if p.content else ""
        lines.append(f"  - [{p.role or '?'}] {p.file}:{p.line}  '{snippet}'  [{risks}]")
    return "\n".join(lines)


@tool
def run_trace_tool(target: str) -> str:
    """数据流追踪。target 格式 'file.py:行:变量'，例：agent.py:21:user_input。

    返回 hop 列表 + 最终判断（safe/warn/danger）。仅在 /investigate 模式下可用。
    """
    err = _consume_investigation_budget("run_trace_tool")
    if err:
        return err
    _audit("run_trace_tool", target=target)

    parts = target.rsplit(":", 2)
    if len(parts) != 3:
        return "错误：参数格式 'file.py:行:变量'，例 agent.py:21:user_input"
    file_part, line_part, var = parts
    try:
        line = int(line_part)
    except ValueError:
        return f"错误：行号不是整数 {line_part!r}"

    file_path = _resolve_within_root(file_part)
    if file_path is None:
        return f"错误：路径越界 {file_part}"
    if not file_path.is_file():
        return f"错误：不是文件 {file_path}"

    from poker.capabilities.trace import trace_var

    try:
        result = trace_var(file_path, line, var, project_root=_project_root)
    except Exception as e:
        return f"错误：trace 失败 {e}"

    lines = [f"trace {target}: verdict={result.overall}, function={result.function_name or '?'}"]
    for hop in result.hops[:30]:
        loc = f"{hop.file or file_part}:{hop.line}"
        lines.append(f"  - {loc}  {hop.var}  {hop.detail}")
    if len(result.hops) > 30:
        lines.append(f"  ... 还有 {len(result.hops) - 30} 条 hop")
    if result.sink_hit is not None:
        lines.append(
            f"  ⚠ 命中危险 sink: {result.sink_hit.name} ({result.sink_hit.severity})"
        )
    return "\n".join(lines)


@tool
def read_findings_tool() -> str:
    """读取最近一次 /scan 的全部 finding（精简）。仅在 /investigate 模式下可用。"""
    err = _consume_investigation_budget("read_findings_tool")
    if err:
        return err
    _audit("read_findings_tool")

    from poker.capabilities.explain import compute_finding_id
    from poker.state import load_last_findings

    findings = load_last_findings(_project_root)
    if not findings:
        return "（最近没有 scan 结果；请先调 run_scan_tool）"

    lines = [f"最近 scan 共 {len(findings)} 条 finding："]
    for f in findings[:100]:
        fid = compute_finding_id(f)
        lines.append(
            f"  [{f.get('severity', ''):>8}] {fid} {f.get('rule_id', '')} "
            f"@ {f.get('path', '')}:{f.get('line', '')}  -  {f.get('title', '')}"
        )
    if len(findings) > 100:
        lines.append(f"  ... 还有 {len(findings) - 100} 条")
    return "\n".join(lines)


def get_investigate_tools() -> list:
    """调查模式的工具集：常规读项目 + 4 个 capability 工具。

    write_file / apply_patch 不暴露给调查（调查只读）。
    """
    return [
        list_files,
        read_file,
        search_text,
        search_code,
        git_diff,
        git_status,
        run_scan_tool,
        run_audit_tool,
        run_trace_tool,
        read_findings_tool,
    ]
