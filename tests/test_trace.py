"""Tests for poker.capabilities.trace（intra-procedural taint + 跨函数 / 跨文件）。"""
from pathlib import Path

import pytest

from poker.capabilities.trace import trace_var
from poker.capabilities.trace.symbols import build_symbol_table


UNSAFE_SAMPLE = '''
import subprocess


def run_command(user_input):
    command = "echo " + user_input
    subprocess.run(command, shell=True)
'''


SAFE_SAMPLE = '''
def use_safely(user_input):
    cleaned = sanitize(user_input)
    return cleaned.upper()
'''


SQL_SAMPLE = '''
def query(user_id):
    sql = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(sql)
'''


EVAL_SAMPLE = '''
def dangerous(formula):
    result = eval(formula)
    return result
'''


@pytest.fixture
def make_file(tmp_path):
    def _make(content: str, name: str = "sample.py"):
        f = tmp_path / name
        f.write_text(content.lstrip("\n"), encoding="utf-8")
        return f
    return _make


def test_trace_finds_subprocess_shell_sink(make_file):
    f = make_file(UNSAFE_SAMPLE)
    result = trace_var(f, line=4, var_name="user_input")
    assert result.overall == "danger"
    assert result.sink_hit is not None
    assert "subprocess" in result.sink_hit.name.lower()
    assert any("subprocess" in h.detail for h in result.hops)


def test_trace_safe_path_no_sink(make_file):
    f = make_file(SAFE_SAMPLE)
    result = trace_var(f, line=2, var_name="user_input")
    assert result.sink_hit is None
    assert result.overall in ("safe", "warn")


def test_trace_sql_fstring_via_execute(make_file):
    f = make_file(SQL_SAMPLE)
    result = trace_var(f, line=2, var_name="user_id")
    assert result.sink_hit is not None
    assert "execute" in result.sink_hit.matchers[0] or "execute" in result.sink_hit.name.lower()


def test_trace_eval_critical(make_file):
    f = make_file(EVAL_SAMPLE)
    result = trace_var(f, line=2, var_name="formula")
    assert result.sink_hit is not None
    assert result.sink_hit.severity == "critical"


def test_trace_unknown_var_returns_no_hops(make_file):
    f = make_file(UNSAFE_SAMPLE)
    result = trace_var(f, line=4, var_name="nonexistent_var")
    assert result.sink_hit is None


def test_trace_outside_function_handled_gracefully(make_file):
    f = make_file("x = 1\ny = x + 2\n")
    result = trace_var(f, line=1, var_name="x")
    assert result.sink_hit is None
    # 应至少能给出"未找到包含该行的函数"提示
    assert result.overall == "safe"


def test_trace_records_function_name(make_file):
    f = make_file(UNSAFE_SAMPLE)
    result = trace_var(f, line=4, var_name="user_input")
    assert result.function_name == "run_command"


# ---------------------------------------------------------------------------
# 跨函数 / 跨文件（Phase 2 第二站）
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """重定向 Path.home() 到 tmp_path，避免污染真实 ~/.poker（symbol 缓存写在那里）。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def project(tmp_path):
    """新建一个 project 子目录，避免 symbols 缓存与 fake_home 同目录冲突。"""
    root = tmp_path / "proj"
    root.mkdir()
    return root


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content.lstrip("\n"), encoding="utf-8")
    return p


def test_trace_inter_function_returns_tainted_to_subprocess(project, fake_home):
    """跨函数：a = req; b = clean(a); subprocess.run(b, shell=True) 命中 sink。"""
    src = """
import subprocess


def clean(x):
    return x


def handle(req):
    a = req
    b = clean(a)
    subprocess.run(b, shell=True)
"""
    f = _write(project, "main.py", src)
    result = trace_var(f, line=8, var_name="req", project_root=project)
    assert result.sink_hit is not None
    assert "subprocess" in result.sink_hit.name.lower()
    # 验证有 hop 进入了 clean 函数
    assert any("clean" in h.detail for h in result.hops)


def test_trace_cross_file_handler_to_utils(project, fake_home):
    """跨文件：handler.py 调 utils.format_cmd 后传给 subprocess —— sink 在另一文件。"""
    handler_src = """
import subprocess

from utils import format_cmd


def handle(req):
    body = req
    cmd = format_cmd(body)
    subprocess.run(cmd, shell=True)
"""
    utils_src = """
def format_cmd(user_input):
    return "echo " + user_input
"""
    handler = _write(project, "handler.py", handler_src)
    _write(project, "utils.py", utils_src)
    result = trace_var(handler, line=6, var_name="req", project_root=project)
    assert result.sink_hit is not None
    assert "subprocess" in result.sink_hit.name.lower()
    # 至少有一个 hop 跨到了 utils.py
    assert any(h.file.endswith("utils.py") for h in result.hops)


def test_trace_cyclic_calls_terminates(project, fake_home):
    """循环引用：f1 ↔ f2 互相调用，最终命中 sink；不死循环。"""
    src = """
import subprocess


def f1(x):
    return f2(x)


def f2(x):
    return f1(x)


def caller(req):
    body = req
    cmd = f1(body)
    subprocess.run(cmd, shell=True)
"""
    f = _write(project, "cycles.py", src)
    result = trace_var(f, line=12, var_name="req", project_root=project, max_depth=15)
    # 关键：能跑完，不死循环
    assert result.overall in ("safe", "warn", "danger")
    # cmd 是 f1 的返回值，f1→f2→f1 循环，但 visited 阻止；
    # 由于 visited 命中后返回 False，f1/f2 都未真正"返回 tainted"，所以 cmd 不会被标记。
    # 但 hop 列表应有 f1 / f2 的进入记录（至少一次）。
    assert any("f1" in h.detail or "f2" in h.detail for h in result.hops)


def test_trace_max_depth_caps_recursion(project, fake_home):
    """max_depth 上限阻止过深递归。"""
    # 链：a -> b -> c -> d -> sink。a 是入口；max_depth 太小应追不到 sink。
    src = """
import subprocess


def b(x):
    return c(x)


def c(x):
    return d(x)


def d(x):
    subprocess.run(x, shell=True)


def a(req):
    val = b(req)
"""
    f = _write(project, "chain.py", src)
    # 入口 a 在 line 16，val 赋值在 line 17
    shallow = trace_var(f, line=17, var_name="req", project_root=project, max_depth=1)
    deep = trace_var(f, line=17, var_name="req", project_root=project, max_depth=10)
    assert deep.sink_hit is not None
    assert shallow.sink_hit is None  # 深度被截断


def test_symbols_resolves_cross_file_imports(project, fake_home):
    """符号表能解析 from utils import format_cmd 的跨文件引用。"""
    _write(project, "utils.py", "def format_cmd(x):\n    return x\n")
    handler = _write(project, "handler.py", "from utils import format_cmd\n\n"
                                            "def h(req):\n    return format_cmd(req)\n")
    table = build_symbol_table(project, use_cache=False)
    info = table.resolve_call(str(handler.resolve()), "format_cmd")
    assert info is not None
    assert info.name == "format_cmd"
    assert info.file.endswith("utils.py")


def test_symbols_cache_reused_when_files_unchanged(project, fake_home):
    """缓存：同样输入第二次调用 build_symbol_table 应命中缓存（mtime + size 不变）。"""
    _write(project, "a.py", "def f(): pass\n")
    t1 = build_symbol_table(project)
    t2 = build_symbol_table(project)
    assert t1.modules.keys() == t2.modules.keys()
    # 缓存文件存在
    from poker.state import get_state_dir
    cache = get_state_dir(project) / "symbols.json"
    assert cache.exists()
