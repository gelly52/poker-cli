"""数据流追踪：跨函数 / 跨文件 taint 分析。

从指定 var 起，按 AST 顺序追踪赋值 / 拼接 / 传参链路，遇到调用项目内已知函数
时按形参映射递归进入；最终检查是否触达 DANGEROUS_SINKS。

跨文件依赖 `symbols.SymbolTable`：扫整个 project_root 一次（带 mtime 缓存），
以解析 import 链 + 函数定位。intra-procedural 模式（不传 project_root）保留旧行为。

关键约束：
  - 调用深度上限（默认 10）防失控
  - 访问过的 (file, func, frozenset(tainted)) 不二次追，避免循环递归
  - 命中 sink 立即停止当前函数追踪
"""
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from poker.capabilities.trace.sinks import DANGEROUS_SINKS, SinkPattern, find_matching_sink
from poker.capabilities.trace.symbols import (
    FunctionInfo,
    SymbolTable,
    build_symbol_table,
)


# ---------- 数据结构 ----------

@dataclass
class Hop:
    line: int
    var: str
    detail: str
    code: str = ""
    file: str = ""        # 跨文件时的源文件（rel path）
    function: str = ""    # 所在函数名


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

DEFAULT_MAX_DEPTH = 10


def trace_var(
    file_path: Path,
    line: int,
    var_name: str,
    project_root: Path | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> TraceResult:
    """从 file_path 的 line 起追踪 var_name 的传播。

    project_root 提供时启用跨函数 + 跨文件追踪；否则仅 intra-procedural（旧行为）。
    """
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

    tracer = _InterTracer(project_root, max_depth=max_depth)
    file_str = str(file_path.resolve())
    # 把入口文件预热进 tracer 缓存（避免重复解析）
    tracer._content_cache[file_str] = content
    tracer._tree_cache[file_str] = tree
    tracer.trace(file_str, func, {var_name}, depth=0, start_line=line)

    overall = "danger" if tracer.sink_hit else ("warn" if tracer.hops else "safe")
    return TraceResult(
        seed_var=var_name, seed_line=line, file=str(file_path),
        function_name=func.name, hops=tracer.hops,
        sink_hit=tracer.sink_hit, overall=overall,
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

    result = trace_var(abs_p, line, var_name, project_root=project_root)
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
    parts: list[str] = []
    node: ast.AST = attr.value
    while isinstance(node, ast.Attribute):
        parts.insert(0, node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.insert(0, node.id)
        return ".".join(parts)
    return ""


# ---------- 追踪核心：跨函数 / 跨文件 ----------

class _InterTracer:
    """单次 trace 的内部状态封装；visited 剪枝 + max_depth 上限防失控。"""

    def __init__(self, project_root: Path | None, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
        self.project_root = project_root.resolve() if project_root else None
        self.max_depth = max_depth
        self.symbols: SymbolTable = (
            build_symbol_table(project_root) if project_root else SymbolTable()
        )
        self.hops: list[Hop] = []
        self.sink_hit: SinkPattern | None = None
        # visited: (abs_file, func_name, frozenset(tainted)) → 已访问
        self.visited: set[tuple[str, str, frozenset[str]]] = set()
        self._content_cache: dict[str, str] = {}
        self._tree_cache: dict[str, Optional[ast.Module]] = {}

    # --- 内部缓存 ---

    def _get_content(self, file: str) -> str:
        if file not in self._content_cache:
            try:
                self._content_cache[file] = Path(file).read_text(encoding="utf-8", errors="replace")
            except OSError:
                self._content_cache[file] = ""
        return self._content_cache[file]

    def _get_lines(self, file: str) -> list[str]:
        return self._get_content(file).splitlines()

    def _get_tree(self, file: str) -> Optional[ast.Module]:
        if file not in self._tree_cache:
            try:
                self._tree_cache[file] = ast.parse(self._get_content(file))
            except SyntaxError:
                self._tree_cache[file] = None
        return self._tree_cache.get(file)

    def _rel(self, file: str) -> str:
        if not self.project_root:
            return file
        try:
            return Path(file).resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return file

    def _get_func_node(self, info: FunctionInfo) -> ast.FunctionDef | None:
        tree = self._get_tree(info.file)
        if tree is None:
            return None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == info.name and node.lineno == info.lineno:
                    return node  # type: ignore[return-value]
        return None

    # --- 主追踪 ---

    def trace(
        self,
        file: str,
        func: ast.FunctionDef,
        tainted: set[str],
        depth: int,
        start_line: int = 0,
    ) -> bool:
        """在 func 内追踪 tainted 的传播；返回 returns_tainted。

        start_line=0 → 追整个函数体；entry function 时设为 seed_line。
        """
        if depth > self.max_depth:
            return False
        key = (file, func.name, frozenset(tainted))
        if key in self.visited:
            return False
        self.visited.add(key)

        rel_file = self._rel(file)
        returns_tainted = False

        for stmt in sorted(_statements_in_function(func), key=lambda n: getattr(n, "lineno", 0)):
            stmt_line = getattr(stmt, "lineno", 0)
            if stmt_line < start_line:
                continue

            if isinstance(stmt, ast.Assign):
                self._handle_assign(stmt, tainted, file, func.name, rel_file, depth)
                if self.sink_hit:
                    return returns_tainted
                continue

            if isinstance(stmt, ast.AugAssign):
                self._handle_aug_assign(stmt, tainted, file, func.name, rel_file, depth)
                if self.sink_hit:
                    return returns_tainted
                continue

            if isinstance(stmt, ast.Return) and stmt.value is not None:
                if self._return_uses_tainted(stmt.value, tainted, file, func.name, rel_file, depth):
                    returns_tainted = True
                if self.sink_hit:
                    return returns_tainted
                continue

            # 其他 stmt：仅处理 Call（含 Expr / If / While / For 体内的 call）
            for call in _calls_in(stmt):
                self._handle_call(call, tainted, file, func.name, rel_file, depth, stmt_line)
                if self.sink_hit:
                    return returns_tainted

        return returns_tainted

    # --- 各 stmt 类型处理 ---

    def _handle_assign(
        self, stmt: ast.Assign, tainted: set[str], file: str, func_name: str,
        rel_file: str, depth: int,
    ) -> None:
        rhs_uses = _names_in(stmt.value) & tainted
        rhs_call_returned = self._process_rhs_calls(stmt.value, tainted, file, func_name, rel_file, depth, stmt.lineno)
        if self.sink_hit:
            return
        if not (rhs_uses or rhs_call_returned):
            return

        line_src = self._line_src(file, stmt.lineno)
        detail_parts = []
        if rhs_uses:
            detail_parts.append(f"赋值（来自 {' / '.join(sorted(rhs_uses))}）")
        if rhs_call_returned:
            detail_parts.append("函数返回污染")

        for tgt in stmt.targets:
            for tgt_name in _names_in(tgt):
                tainted.add(tgt_name)
                self.hops.append(Hop(
                    line=stmt.lineno, var=tgt_name,
                    detail=" / ".join(detail_parts),
                    code=line_src, file=rel_file, function=func_name,
                ))

    def _handle_aug_assign(
        self, stmt: ast.AugAssign, tainted: set[str], file: str, func_name: str,
        rel_file: str, depth: int,
    ) -> None:
        rhs_uses = _names_in(stmt.value) & tainted
        rhs_call_returned = self._process_rhs_calls(stmt.value, tainted, file, func_name, rel_file, depth, stmt.lineno)
        if self.sink_hit:
            return
        if not (rhs_uses or rhs_call_returned):
            return
        line_src = self._line_src(file, stmt.lineno)
        for n in _names_in(stmt.target):
            tainted.add(n)
            detail = (
                f"复合赋值（来自 {' / '.join(sorted(rhs_uses))}）" if rhs_uses
                else "复合赋值（函数返回污染）"
            )
            self.hops.append(Hop(
                line=stmt.lineno, var=n, detail=detail,
                code=line_src, file=rel_file, function=func_name,
            ))

    def _return_uses_tainted(
        self, value: ast.expr, tainted: set[str], file: str, func_name: str,
        rel_file: str, depth: int,
    ) -> bool:
        name_match = bool(_names_in(value) & tainted)
        # 始终处理 value 中的 Call（即便 names 已命中），保证 sink 检测 + 跨函数下钻不丢
        call_returned = self._process_rhs_calls(
            value, tainted, file, func_name, rel_file, depth,
            getattr(value, "lineno", 0),
        )
        return name_match or call_returned

    def _process_rhs_calls(
        self, value: ast.expr, tainted: set[str], file: str, func_name: str,
        rel_file: str, depth: int, stmt_line: int,
    ) -> bool:
        """RHS / return 表达式中的 Call：sink 检测 + 跨函数下钻；返回是否有任一 call 返回 tainted。"""
        any_returned = False
        for call in _calls_in(value):
            _, returned = self._handle_call(call, tainted, file, func_name, rel_file, depth, stmt_line)
            if returned:
                any_returned = True
            if self.sink_hit:
                return any_returned
        return any_returned

    def _handle_call(
        self, call: ast.Call, tainted: set[str], file: str, func_name: str,
        rel_file: str, depth: int, stmt_line: int,
    ) -> tuple[bool, bool]:
        """处理单个 Call：sink 检测 + 跨函数下钻。

        返回 (hit_sink, returned_tainted)：
          - hit_sink: 命中危险 sink（已写入 self.sink_hit）
          - returned_tainted: 进入项目函数后该函数 return 表达式含 tainted
        """
        arg_names: set[str] = set()
        for arg in call.args:
            arg_names.update(_names_in(arg))
        for kw in call.keywords:
            arg_names.update(_names_in(kw.value))
        tainted_args = arg_names & tainted
        if not tainted_args:
            return False, False

        call_name = _call_name(call)
        call_line = getattr(call, "lineno", stmt_line)
        line_src = self._line_src(file, call_line)

        # 1. sink 命中
        sink = find_matching_sink(call_name)
        if sink:
            self.hops.append(Hop(
                line=call_line, var=", ".join(sorted(tainted_args)),
                detail=f"传给 {call_name}（命中 sink: {sink.name}）",
                code=line_src, file=rel_file, function=func_name,
            ))
            self.sink_hit = sink
            return True, False

        # 2. 普通调用 hop
        self.hops.append(Hop(
            line=call_line, var=", ".join(sorted(tainted_args)),
            detail=f"传参到 {call_name}",
            code=line_src, file=rel_file, function=func_name,
        ))

        # 3. 项目内函数 → 下钻
        target_info = self.symbols.resolve_call(file, call_name)
        if target_info is None:
            return False, False
        callee_tainted = self._map_tainted_to_params(call, tainted, target_info)
        if not callee_tainted:
            return False, False
        callee_node = self._get_func_node(target_info)
        if callee_node is None:
            return False, False

        callee_rel = self._rel(target_info.file)
        self.hops.append(Hop(
            line=target_info.lineno, var=", ".join(sorted(callee_tainted)),
            detail=f"进入 {callee_rel}:{target_info.name}（depth {depth + 1}）",
            code="", file=callee_rel, function=target_info.name,
        ))
        returned_tainted = self.trace(target_info.file, callee_node, callee_tainted, depth + 1)
        if self.sink_hit:
            return True, returned_tainted
        return False, returned_tainted

    def _map_tainted_to_params(
        self, call: ast.Call, caller_tainted: set[str], target: FunctionInfo,
    ) -> set[str]:
        """把 call 的位置参数 / 命名参数中的 tainted 映射到 target 的形参名。"""
        result: set[str] = set()
        for idx, arg in enumerate(call.args):
            if idx >= len(target.params):
                break
            if _names_in(arg) & caller_tainted:
                result.add(target.params[idx])
        for kw in call.keywords:
            if kw.arg is None:
                continue
            if (_names_in(kw.value) & caller_tainted) and kw.arg in target.params:
                result.add(kw.arg)
        return result

    def _line_src(self, file: str, line: int) -> str:
        lines = self._get_lines(file)
        return lines[line - 1].strip() if 0 < line <= len(lines) else ""


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
        loc = f"{hop.file}:{hop.line}" if hop.file else f"line {hop.line}"
        func_part = f":{hop.function}" if hop.function else ""
        console.print(f"  → {loc}{func_part}: [bold]{hop.var}[/bold] - {hop.detail}")
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
