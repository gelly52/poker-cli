"""项目级符号表：function 定义 + import 链 + 跨文件函数解析。

为跨函数 / 跨文件 trace 服务。一次扫整个 project_root，按文件 mtime + size 缓存
到 `.poker/state/<hash>/symbols.json`；任一文件失效 → 整体重建（YAGNI，不做增量）。

简化决策：
  - 仅顶层 def + 类内 def（qualname: `module.func` 或 `module.Class.func`）
  - import 形态：`import X`、`import X as Y`、`from M import f`、`from M import f as g`
  - 不解析嵌套函数 / lambda / 装饰器变换 / 动态 getattr
"""
import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from poker.state import get_state_dir
from poker.workspace import iter_text_files

_SYMBOLS_FILE = "symbols.json"
_SCHEMA_VERSION = 1


@dataclass
class FunctionInfo:
    """一个函数的位置 + 形参签名。"""
    name: str
    file: str
    lineno: int
    end_lineno: int
    params: list[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    qualname: str  # 'pkg.sub.mod'
    file: str  # absolute path (str)
    mtime: float = 0.0
    size: int = 0
    functions: dict[str, FunctionInfo] = field(default_factory=dict)
    imports: dict[str, str] = field(default_factory=dict)
    # imports 同时存两类 ref：
    #   'utils' -> 'utils'             （import utils）
    #   'fmt'   -> 'utils.format_cmd'  （from utils import format_cmd as fmt）


@dataclass
class SymbolTable:
    modules: dict[str, ModuleInfo] = field(default_factory=dict)
    file_to_module: dict[str, str] = field(default_factory=dict)  # abs file -> qualname

    def lookup_in_file(self, file: str, name: str) -> Optional[FunctionInfo]:
        """在 file 所属 module 找 name 函数。"""
        qn = self.file_to_module.get(str(Path(file).resolve()))
        if not qn:
            return None
        return self.modules[qn].functions.get(name)

    def resolve_call(self, caller_file: str, call_name: str) -> Optional[FunctionInfo]:
        """根据调用者所在文件 + 调用名解析到具体 FunctionInfo。

        call_name 形态：
          'foo'        → 当前 module 的 foo（或 from-import 进来的 foo）
          'utils.foo'  → import utils 后调 utils.foo
          'fmt'        → from utils import format_cmd as fmt
          '.execute'   → 方法调用，跨文件解析不可，返回 None
        """
        if call_name.startswith(".") or call_name == "<unknown>":
            return None
        caller_qn = self.file_to_module.get(str(Path(caller_file).resolve()))
        if not caller_qn:
            return None
        mod = self.modules[caller_qn]

        if "." not in call_name:
            if call_name in mod.imports:
                return self._lookup_qualified(mod.imports[call_name])
            if call_name in mod.functions:
                return mod.functions[call_name]
            return None

        head, _, tail = call_name.partition(".")
        if head in mod.imports:
            base = mod.imports[head]
            return self._lookup_qualified(f"{base}.{tail}")
        return None

    def _lookup_qualified(self, qualref: str) -> Optional[FunctionInfo]:
        """按 'module.func' / 'pkg.mod.Class.func' 在 modules 里找。"""
        parts = qualref.split(".")
        for i in range(len(parts) - 1, 0, -1):
            mod_name = ".".join(parts[:i])
            func_name = ".".join(parts[i:])
            mod = self.modules.get(mod_name)
            if mod and func_name in mod.functions:
                return mod.functions[func_name]
        return None


def build_symbol_table(project_root: Path, use_cache: bool = True) -> SymbolTable:
    """扫整个 project_root 建符号表；mtime + size 全部对得上 → 复用缓存。"""
    project_root = project_root.resolve()
    py_files = sorted(f for f in iter_text_files(project_root) if f.suffix == ".py")

    cache_path = get_state_dir(project_root) / _SYMBOLS_FILE
    if use_cache and cache_path.exists():
        cached = _try_load_cached(cache_path, py_files)
        if cached is not None:
            return cached

    table = SymbolTable()
    for f in py_files:
        mod = _parse_module(f, project_root)
        if mod:
            table.modules[mod.qualname] = mod
            table.file_to_module[mod.file] = mod.qualname

    try:
        _save_cache(cache_path, table)
    except OSError:
        pass
    return table


def _parse_module(file_path: Path, project_root: Path) -> Optional[ModuleInfo]:
    """解析一个 .py 文件 → ModuleInfo（解析失败返回 None）。"""
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
        stat = file_path.stat()
    except (SyntaxError, OSError):
        return None

    rel = file_path.resolve().relative_to(project_root)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    qualname = ".".join(parts) if parts else file_path.stem

    mod = ModuleInfo(
        qualname=qualname,
        file=str(file_path.resolve()),
        mtime=stat.st_mtime,
        size=stat.st_size,
    )

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mod.functions[node.name] = _function_info(node, mod.file)
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mod.functions[f"{node.name}.{sub.name}"] = _function_info(sub, mod.file)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                mod.imports[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                for alias in node.names:
                    local = alias.asname or alias.name
                    mod.imports[local] = f"{node.module}.{alias.name}"
    return mod


def _function_info(func, file: str) -> FunctionInfo:
    return FunctionInfo(
        name=func.name,
        file=file,
        lineno=func.lineno,
        end_lineno=getattr(func, "end_lineno", func.lineno),
        params=[a.arg for a in func.args.args],
    )


def _try_load_cached(cache_path: Path, py_files: list[Path]) -> Optional[SymbolTable]:
    """缓存 mtime + size 全部对得上 → 反序列化复用，否则 None。"""
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema") != _SCHEMA_VERSION:
        return None

    current = {}
    for f in py_files:
        try:
            stat = f.stat()
        except OSError:
            continue
        current[str(f.resolve())] = (stat.st_mtime, stat.st_size)

    cached_mods = data.get("modules", {})
    cached = {m["file"]: (m["mtime"], m["size"]) for m in cached_mods.values()}
    if current != cached:
        return None

    table = SymbolTable()
    for qn, m in cached_mods.items():
        functions = {n: FunctionInfo(**fi) for n, fi in m.get("functions", {}).items()}
        mod = ModuleInfo(
            qualname=m["qualname"], file=m["file"], mtime=m["mtime"], size=m["size"],
            functions=functions, imports=m.get("imports", {}),
        )
        table.modules[qn] = mod
        table.file_to_module[mod.file] = qn
    return table


def _save_cache(cache_path: Path, table: SymbolTable) -> None:
    payload = {
        "schema": _SCHEMA_VERSION,
        "modules": {
            qn: {
                "qualname": m.qualname, "file": m.file, "mtime": m.mtime, "size": m.size,
                "functions": {n: asdict(fi) for n, fi in m.functions.items()},
                "imports": m.imports,
            }
            for qn, m in table.modules.items()
        },
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
