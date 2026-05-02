"""Tests for poker.capabilities.redteam.endpoints."""
import pytest

from poker.capabilities.redteam.endpoints import (
    env_var_name,
    load_endpoints,
    resolve_api_key,
)


SAMPLE_TOML = '''
[endpoints.local_mock]
url = "http://localhost:8080/v1/chat/completions"
model = "test-model"
rate_limit = 2.0

[endpoints.remote_test]
url = "https://api.example.com/v1/chat"
model = "gpt-4"

[endpoints.bad_url]
url = "ftp://bad.example.com"
model = "x"

[endpoints.bad_url2]
url = "file:///etc/passwd"

[endpoints."with-dash"]
url = "https://x.example.com"
'''


@pytest.fixture
def endpoints_file(tmp_path):
    f = tmp_path / "redteam_endpoints.toml"
    f.write_text(SAMPLE_TOML.lstrip("\n"), encoding="utf-8")
    return f


def test_load_endpoints_picks_valid(endpoints_file):
    eps = load_endpoints(endpoints_file)
    assert "local_mock" in eps
    assert "remote_test" in eps
    assert eps["local_mock"].url.startswith("http://")
    assert eps["local_mock"].rate_limit == 2.0
    assert eps["remote_test"].rate_limit == 1.0  # 默认


def test_load_endpoints_rejects_non_http_scheme(endpoints_file):
    eps = load_endpoints(endpoints_file)
    assert "bad_url" not in eps   # ftp:// rejected
    assert "bad_url2" not in eps  # file:// rejected


def test_load_endpoints_rejects_bad_name(endpoints_file):
    """名字含 dash 不合法（防止 env var / 文件名问题）。"""
    eps = load_endpoints(endpoints_file)
    assert "with-dash" not in eps


def test_load_endpoints_missing_file(tmp_path):
    eps = load_endpoints(tmp_path / "does_not_exist.toml")
    assert eps == {}


def test_load_endpoints_corrupt_toml(tmp_path):
    """损坏的 TOML 安静返回空字典，不抛。"""
    f = tmp_path / "bad.toml"
    f.write_text("not valid [[[ toml", encoding="utf-8")
    assert load_endpoints(f) == {}


def test_resolve_api_key_from_env(monkeypatch):
    monkeypatch.setenv("POKER_REDTEAM_LOCAL_MOCK_KEY", "sk-test-123")
    assert resolve_api_key("local_mock") == "sk-test-123"


def test_resolve_api_key_missing(monkeypatch):
    monkeypatch.delenv("POKER_REDTEAM_NOTSET_KEY", raising=False)
    assert resolve_api_key("notset") is None


def test_resolve_api_key_empty_string(monkeypatch):
    """空白 / 仅空格的 env var 视为未设置。"""
    monkeypatch.setenv("POKER_REDTEAM_BLANK_KEY", "   ")
    assert resolve_api_key("blank") is None


def test_resolve_api_key_rejects_bad_name():
    """非法 name 不参与解析，避免 env 注入。"""
    assert resolve_api_key("bad-name") is None
    assert resolve_api_key("../etc/passwd") is None


def test_env_var_name_format():
    assert env_var_name("local_mock") == "POKER_REDTEAM_LOCAL_MOCK_KEY"
    assert env_var_name("DeepSeek") == "POKER_REDTEAM_DEEPSEEK_KEY"
