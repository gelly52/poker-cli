"""Tests for poker.capabilities.trace（intra-procedural taint）。"""
import pytest

from poker.capabilities.trace import trace_var


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
