"""审计 agent tools 维度：识别 @tool 定义 → 静态风险检查 → 可选 LLM 评估。"""
import ast
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from poker.capabilities.audit._common import (
    Risk,
    SEVERITY_RANK as _SEVERITY_RANK,
    SEVERITY_STYLES as _SEVERITY_STYLES,
)
from poker.state import save_audit
from poker.workspace import iter_text_files


# ---------- 数据结构 ----------

@dataclass
class ParamInfo:
    name: str
    annotation: str = ""
    has_validator: bool = False


@dataclass
class ToolInfo:
    name: str
    file: str
    line: int
    framework: str
    decorator: str
    docstring: str
    params: list[ParamInfo] = field(default_factory=list)
    source: str = ""


@dataclass
class AuditResult:
    tool: ToolInfo
    risks: list[Risk] = field(default_factory=list)
    overall_severity: str = "info"
    llm_summary: str = ""


# ---------- AST: 识别 @tool ----------

def find_tools(project_root: Path) -> list[ToolInfo]:
    """扫描项目内的 @tool 装饰函数（MVP 主要识别 LangChain @tool）。"""
    tools: list[ToolInfo] = []
    for py_file in iter_text_files(project_root):
        if py_file.suffix != ".py":
            continue
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if _has_tool_decorator(node):
                    tools.append(_build_tool_info(node, py_file, project_root))
    return tools


def _has_tool_decorator(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "tool":
            return True
        if isinstance(dec, ast.Call):
            if isinstance(dec.func, ast.Name) and dec.func.id == "tool":
                return True
            if isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool":
                return True
        if isinstance(dec, ast.Attribute) and dec.attr == "tool":
            return True
    return False


def _build_tool_info(node: ast.FunctionDef, file: Path, project_root: Path) -> ToolInfo:
    docstring = ast.get_docstring(node) or ""
    params: list[ParamInfo] = []
    for arg in node.args.args:
        annotation = ""
        if arg.annotation is not None:
            try:
                annotation = ast.unparse(arg.annotation)
            except Exception:
                annotation = ""
        params.append(ParamInfo(
            name=arg.arg,
            annotation=annotation,
            has_validator=_has_validation(node, arg.arg),
        ))

    try:
        source = ast.unparse(node)
    except Exception:
        source = ""

    try:
        rel_file = file.relative_to(project_root).as_posix()
    except ValueError:
        rel_file = str(file)

    return ToolInfo(
        name=node.name,
        file=rel_file,
        line=node.lineno,
        framework="langchain",
        decorator="@tool",
        docstring=docstring,
        params=params,
        source=source,
    )


def _has_validation(func: ast.FunctionDef, param_name: str) -> bool:
    """启发式：函数体内是否对 param 做了 isinstance / assert / 显式判断。"""
    for child in ast.walk(func):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and child.func.id == "isinstance":
            for arg in child.args:
                if isinstance(arg, ast.Name) and arg.id == param_name:
                    return True
        if isinstance(child, ast.Assert):
            for sub in ast.walk(child):
                if isinstance(sub, ast.Name) and sub.id == param_name:
                    return True
    return False


# ---------- 静态风险检查 ----------

_DANGER_PATTERNS = [
    (re.compile(r"subprocess\.[A-Za-z_]+\([^)]*shell\s*=\s*True", re.MULTILINE),
     "shell_exec", "high",
     "调用 subprocess 时启用 shell=True，参数被当 shell 解释",
     "禁用 shell=True；必须时对参数做白名单 / shlex.quote 校验"),
    (re.compile(r"\b(eval|exec)\s*\(", re.MULTILINE),
     "dynamic_exec", "critical",
     "使用 eval / exec 执行动态代码",
     "避免 eval / exec；用安全的 ast 解析或受限解释器"),
    (re.compile(r"os\.system\s*\(", re.MULTILINE),
     "os_system", "high",
     "调用 os.system，参数当 shell 解释",
     "改用 subprocess.run([list], shell=False)"),
    (re.compile(r"\.execute\s*\(\s*f[\"']", re.MULTILINE),
     "sql_fstring", "high",
     "用 f-string 构建 SQL 后 execute，存在注入风险",
     "改用参数化查询：cursor.execute(sql, (params,))"),
    (re.compile(r"open\s*\([^)]*[\"']w[ab+]?[\"']", re.MULTILINE),
     "file_write", "medium",
     "工具内有写文件操作",
     "确认是否需要写权限；MVP 范围内的工具应只读"),
]


def audit_tool(tool_info: ToolInfo, llm: Any = None) -> AuditResult:
    """对单个 tool 做静态 + 可选 LLM 风险评估。"""
    risks: list[Risk] = []

    # 参数检查
    for p in tool_info.params:
        if p.name in ("self", "cls"):
            continue
        if not p.annotation:
            risks.append(Risk(
                check="missing_type_hint",
                severity="low",
                evidence=f"参数 {p.name} 无类型注解",
                recommendation="添加 type hint，便于 LLM 正确调用并约束输入",
            ))
        elif p.annotation in ("str", "Any") and not p.has_validator:
            risks.append(Risk(
                check="missing_validator",
                severity="medium",
                evidence=f"参数 {p.name}: {p.annotation}，函数体内无显式校验",
                recommendation="对外部输入做格式 / 范围校验（Pydantic、白名单等）",
            ))

    # 危险模式
    for regex, check, severity, evidence, recommendation in _DANGER_PATTERNS:
        if regex.search(tool_info.source):
            risks.append(Risk(check=check, severity=severity, evidence=evidence, recommendation=recommendation))

    # docstring 检查
    if not tool_info.docstring:
        risks.append(Risk(
            check="missing_docstring",
            severity="low",
            evidence="工具无 docstring，LLM 难以正确选用",
            recommendation="添加简洁明确的 docstring：用途、输入、输出、副作用",
        ))
    elif len(tool_info.docstring) < 20:
        risks.append(Risk(
            check="vague_docstring",
            severity="low",
            evidence=f"docstring 过短: {tool_info.docstring!r}",
            recommendation="扩展描述：用途、输入约束、副作用、错误情况",
        ))

    # HITL: 有副作用但无确认
    src = tool_info.source
    has_side_effect = any(p in src for p in ("subprocess", "os.system", "open(", "requests.", "urllib", "socket"))
    has_confirmation = any(p in src for p in ("input(", "confirm", "approve", "yes_no", "Confirm"))
    if has_side_effect and not has_confirmation:
        risks.append(Risk(
            check="missing_hitl",
            severity="medium",
            evidence="工具有副作用（IO / 网络 / 进程）但无用户确认机制",
            recommendation="对高风险动作加 human-in-the-loop 确认或 allowlist",
        ))

    # LLM 评估（可选）
    llm_summary = ""
    if llm is not None and tool_info.docstring:
        try:
            llm_summary = _llm_assess_description(tool_info, llm)
        except Exception as e:
            llm_summary = f"（LLM 评估失败：{e}）"

    overall = "info"
    for r in risks:
        if _SEVERITY_RANK[r.severity] < _SEVERITY_RANK[overall]:
            overall = r.severity

    return AuditResult(tool=tool_info, risks=risks, overall_severity=overall, llm_summary=llm_summary)


def _llm_assess_description(tool_info: ToolInfo, llm: Any) -> str:
    """让 LLM 评估 tool 描述模糊度和潜在风险。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    sys_prompt = (
        "你是一个 AI 应用安全审计员。请评估给定 tool 的描述是否清晰、是否暴露过度权限、"
        "是否容易被 prompt injection 误用。给出 3-5 条简短结论，每条一行。"
    )
    user_msg = (
        f"工具名: {tool_info.name}\n"
        f"装饰器: {tool_info.decorator}\n"
        f"参数: {[p.name + ':' + p.annotation for p in tool_info.params]}\n"
        f"描述: {tool_info.docstring}\n\n"
        f"源码片段（前 1500 字符）:\n{tool_info.source[:1500]}"
    )
    response = llm.invoke([
        SystemMessage(content=sys_prompt),
        HumanMessage(content=user_msg),
    ])
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


# ---------- 交互式主流程 ----------

def interactive_audit_tools(project_root: Path, llm: Any, console: Console) -> None:
    """/audit tools 主流程。"""
    tools = find_tools(project_root)
    if not tools:
        console.print("[yellow]未发现 @tool 装饰的函数（MVP 只识别 LangChain @tool）[/yellow]")
        return

    table = Table(title=f"发现 {len(tools)} 个 tool")
    table.add_column("#", style="bold")
    table.add_column("名称")
    table.add_column("位置")
    table.add_column("框架")
    for i, t in enumerate(tools, 1):
        table.add_row(str(i), t.name, f"{t.file}:{t.line}", t.framework)
    console.print(table)

    selected = _prompt_selection(tools, console)
    if selected is None:
        return

    for t in selected:
        console.print(f"\n[bold]审计 {t.name}[/bold] [dim]({t.file}:{t.line})[/dim]")
        result = audit_tool(t, llm)
        _render_audit_result(console, result)
        path = save_audit(project_root, "tools", t.name, _result_to_dict(result))
        console.print(f"[dim]结果已保存：{path}[/dim]")


def _prompt_selection(tools: list[ToolInfo], console: Console) -> list[ToolInfo] | None:
    """让用户选择审计哪个工具。返回 None 表示取消。"""
    while True:
        try:
            choice = input("选择要审计的工具（编号 / 名称 / all / quit）：").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        if not choice:
            continue
        if choice.startswith("/") or choice.startswith("!"):
            console.print(f"[yellow]当前在审计选择中，先输 'quit' 退出再运行 {choice}[/yellow]")
            continue
        if choice in ("quit", "q", "exit"):
            return None
        if choice == "all":
            return tools
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(tools):
                return [tools[idx]]
            console.print(f"[red]编号超出范围 1..{len(tools)}[/red]")
            continue
        matched = [t for t in tools if t.name == choice]
        if matched:
            return matched
        console.print(f"[red]找不到名为 {choice} 的工具[/red]")


def _render_audit_result(console: Console, result: AuditResult) -> None:
    style = _SEVERITY_STYLES.get(result.overall_severity, "white")
    console.print(f"  综合等级: [{style}]{result.overall_severity.upper()}[/{style}]")

    if not result.risks:
        console.print("  [green]未发现明显风险[/green]")
    else:
        for r in result.risks:
            s = _SEVERITY_STYLES.get(r.severity, "white")
            console.print(f"  [{s}][{r.severity}][/{s}] {r.check}: {r.evidence}")
            console.print(f"      → {r.recommendation}")

    if result.llm_summary:
        console.print(f"\n  [dim]LLM 评估:[/dim]")
        for line in result.llm_summary.splitlines():
            if line.strip():
                console.print(f"    {line}")


def _result_to_dict(result: AuditResult) -> dict:
    """序列化 AuditResult 给 state.save_audit 使用。"""
    return {
        "tool": asdict(result.tool),
        "risks": [asdict(r) for r in result.risks],
        "overall_severity": result.overall_severity,
        "llm_summary": result.llm_summary,
    }
