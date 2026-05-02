"""数据流追踪：函数内 (intra-procedural) taint 分析。

从指定 var 起，按 AST 顺序追踪赋值 / 拼接 / 传参链路，检查是否触达 DANGEROUS_SINKS。
"""
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poker.capabilities.trace.sinks import DANGEROUS_SINKS, SinkPattern, find_matching_sink


# ---------- 数据结构 ----------

@dataclass
class Hop:
    line: int
    var: str
    detail: str
    code: str = ""


@dataclass
class TraceResult:
    seed_var: str
    seed_line: int
    file: str
    function_name: str = ""
    hops: list[Hop] = field(default_factory=list)
    sink_hit: SinkPattern | None = None
    overall: str = "safe"  # safe | warn | danger


# ---------- 主入口 ----------

def trace_var(file_path: Path, line: int, var_name: str) -> TraceResult:
    """在 file_path 的 line 所在函数内追踪 var_name 的传播。"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except (SyntaxError, OSError) as e:
        return TraceResult(
            seed_var=var_name, seed_line=line, file=str(file_path),
            hops=[Hop(line=line, var=var_name, detail=f"解析失败: {e}", code="")],
            overall="safe",
        )

    func = _find_enclosing_function(tree, line)
    if func is None:
        return TraceResult(
            seed_var=var_name, seed_line=line, file=str(file_path),
            hops=[Hop(line=line, var=var_name, detail="未找到包含该行的函数", code="")],
            overall="safe",
        )

    hops, sink_hit = _track(func, var_name, line, content)
    overall = "danger" if sink_hit else ("warn" if hops else "safe")
    return TraceResult(
        seed_var=var_name, seed_line=line, file=str(file_path),
        function_name=func.name, hops=hops, sink_hit=sink_hit, overall=overall,
    )


def run_trace(target_str: str, project_root: Path, console: Any) -> None:
    """解析 '文件:行:变量' 并跑 trace_var，渲染结果到 console。"""
    parts = target_str.split(":")
    if len(parts) != 3:
        console.print(f"[red]格式错误：期望 '文件:行:变量'，得到 {target_str!r}[/red]")
        return
    file_str, line_str, var_name = parts[0], parts[1], parts[2]
    try:
        line = int(line_str)
    except ValueError:
        console.print(f"[red]行号不是整数：{line_str}[/red]")
        return

    p = Path(file_str).expanduser()
    abs_p = p.resolve() if p.is_absolute() else (project_root / p).resolve()
    try:
        abs_p.relative_to(project_root)
    except ValueError:
        console.print(f"[red]路径越界：{file_str} 不在 project root ({project_root}) 内[/red]")
        return
    if not abs_p.is_file():
        console.print(f"[red]文件不存在：{abs_p}[/red]")
        return

    result = trace_var(abs_p, line, var_name)
    _render_trace_result(result, project_root, console)


# ---------- AST 工具 ----------

def _find_enclosing_function(tree: ast.Module, line: int) -> ast.FunctionDef | None:
    """找到包含 line 的最深层函数。"""
    candidate: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if start <= line <= end:
                if candidate is None or node.lineno > candidate.lineno:
                    candidate = node
    return candidate


def _statements_in_function(func: ast.FunctionDef) -> list[ast.stmt]:
    """收集 func 体内所有 stmt（不进入嵌套函数）。"""
    stmts: list[ast.stmt] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not func:
                continue
            if isinstance(child, ast.stmt):
                stmts.append(child)
            walk(child)

    walk(func)
    return stmts


def _names_in(node: ast.AST) -> set[str]:
    """提取 AST 节点中所有 Name 标识符。"""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.arg):
            names.add(sub.arg)
    return names


def _calls_in(node: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _call_name(call: ast.Call) -> str:
    """获取 call 的函数名：'subprocess.run' / 'eval' / '.execute' 等。"""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        base = _attr_base(func)
        if base:
            return f"{base}.{func.attr}"
        return f".{func.attr}"
    return "<unknown>"


def _attr_base(attr: ast.Attribute) -> str:
    """递归获取 Attribute 的 base 名称（a.b.c -> 'a.b'）。"""
    parts: list[str] = []
    node: ast.AST = attr.value
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.insert(0, node.id)
        return ".".join(parts)
    return ""


# ---------- 追踪核心 ----------

def _track(func: ast.FunctionDef, seed: str, seed_line: int, content: str) -> tuple[list[Hop], SinkPattern | None]:
    """从 seed_line 开始追踪 seed 的传播；返回 hops 和命中的 sink（若有）。"""
    tainted: set[str] = {seed}
    hops: list[Hop] = []
    lines = content.splitlines()
    sink_hit: SinkPattern | None = None

    statements = sorted(_statements_in_function(func), key=lambda n: getattr(n, "lineno", 0))

    for stmt in statements:
        stmt_line = getattr(stmt, "lineno", 0)
        if stmt_line < seed_line:
            continue

        # 赋值：RHS 用到 tainted → LHS 变 tainted
        if isinstance(stmt, ast.Assign):
            rhs_uses = _names_in(stmt.value) & tainted
            if rhs_uses:
                line_src = lines[stmt_line - 1].strip() if stmt_line <= len(lines) else ""
                for tgt in stmt.targets:
                    for tgt_name in _names_in(tgt):
                        tainted.add(tgt_name)
                        hops.append(Hop(
                            line=stmt_line, var=tgt_name,
                            detail=f"赋值（来自 {' / '.join(sorted(rhs_uses))}）",
                            code=line_src,
                        ))

        elif isinstance(stmt, ast.AugAssign):
            rhs_uses = _names_in(stmt.value) & tainted
            if rhs_uses:
                line_src = lines[stmt_line - 1].strip() if stmt_line <= len(lines) else ""
                for n in _names_in(stmt.target):
                    tainted.add(n)
                    hops.append(Hop(
                        line=stmt_line, var=n,
                        detail=f"复合赋值（来自 {' / '.join(sorted(rhs_uses))}）",
                        code=line_src,
                    ))

        # 调用：任一 arg 是 tainted → 检查 sink
        for call in _calls_in(stmt):
            arg_names: set[str] = set()
            for arg in call.args:
                arg_names.update(_names_in(arg))
            for kw in call.keywords:
                arg_names.update(_names_in(kw.value))
            tainted_args = arg_names & tainted
            if not tainted_args:
                continue

            call_name = _call_name(call)
            call_line = getattr(call, "lineno", stmt_line)
            line_src = lines[call_line - 1].strip() if call_line <= len(lines) else ""
            sink = find_matching_sink(call_name)
            if sink:
                hops.append(Hop(
                    line=call_line,
                    var=", ".join(sorted(tainted_args)),
                    detail=f"传给 {call_name}（命中 sink: {sink.name}）",
                    code=line_src,
                ))
                return hops, sink
            hops.append(Hop(
                line=call_line,
                var=", ".join(sorted(tainted_args)),
                detail=f"传参到 {call_name}",
                code=line_src,
            ))

    return hops, sink_hit


# ---------- 渲染 ----------

_OVERALL_STYLES = {"safe": "green", "warn": "yellow", "danger": "red"}


def _render_trace_result(result: TraceResult, project_root: Path, console: Any) -> None:
    try:
        rel = Path(result.file).relative_to(project_root).as_posix()
    except (ValueError, TypeError):
        rel = result.file

    func_str = f"  函数: {result.function_name}" if result.function_name else ""
    console.print(f"\n[bold]Trace: {result.seed_var} @ {rel}:{result.seed_line}[/bold]{func_str}")

    if not result.hops:
        console.print("  [green]未发现传播路径（变量未被使用 / 函数内无后续传播）[/green]")
        return

    for hop in result.hops:
        console.print(f"  → line {hop.line}: [bold]{hop.var}[/bold] - {hop.detail}")
        if hop.code:
            console.print(f"      [dim]{hop.code}[/dim]")

    if result.sink_hit:
        s = result.sink_hit
        console.print(f"\n[red]⚠️  触达危险 sink: {s.name} ({s.severity})[/red]")
        console.print(f"  描述: {s.description}")
        console.print(f"  建议: {s.recommendation}")
    else:
        style = _OVERALL_STYLES.get(result.overall, "white")
        console.print(f"\n[{style}]结果: {result.overall.upper()}[/{style}]")
