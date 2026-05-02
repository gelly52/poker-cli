"""Tests for config module."""
import os
from pathlib import Path

from poker.config.loader import _load_toml, load_config, save_project_config
from poker.config.models import PokerConfig, ProviderConfig


def test_provider_config_redacted_key_empty() -> None:
    """空 key 显示为未设置。"""
    p = ProviderConfig()
    assert p.redacted_key() == "<未设置>"


def test_provider_config_redacted_key_short() -> None:
    """短 key 全部遮盖。"""
    p = ProviderConfig(api_key="abc")
    assert p.redacted_key() == "***"


def test_provider_config_redacted_key_normal() -> None:
    """正常 key 保留首尾3字符。"""
    p = ProviderConfig(api_key="sk-abcdefghijklmnopqrstuvwxyz1234567890")
    assert p.redacted_key() == "sk-***890"


def test_poker_config_has_api_key_false() -> None:
    """无 API key 时 LLM 不就绪。"""
    config = PokerConfig()
    assert config.has_api_key is False


def test_poker_config_has_api_key_true() -> None:
    """有 API key 时 LLM 就绪。"""
    config = PokerConfig(provider=ProviderConfig(api_key="sk-test"))
    assert config.has_api_key is True


def test_load_toml_parses_simple(tmp_path: Path) -> None:
    """简易 TOML 解析。"""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text('provider.name = "deepseek"\nprovider.model = "deepseek-chat"\n', encoding="utf-8")

    data = _load_toml(toml_file)
    assert data["provider.name"] == "deepseek"
    assert data["provider.model"] == "deepseek-chat"


def test_load_toml_skips_comments_and_blank(tmp_path: Path) -> None:
    """跳过注释和空行。"""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("# comment\n\nprovider.name = \"anthropic\"\n", encoding="utf-8")

    data = _load_toml(toml_file)
    assert data == {"provider.name": "anthropic"}


def test_load_toml_missing_file(tmp_path: Path) -> None:
    """文件不存在返回空 dict。"""
    data = _load_toml(tmp_path / "nonexistent.toml")
    assert data == {}


def test_load_config_defaults(tmp_path: Path, monkeypatch) -> None:
    """无配置文件时使用默认值。"""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("POKER_PROVIDER", raising=False)
    config = load_config(project_root=tmp_path)
    assert config.provider.name == "openai"
    assert config.profile == "default"


def test_load_config_env_override(tmp_path: Path, monkeypatch) -> None:
    """环境变量优先级最高。"""
    monkeypatch.setenv("POKER_PROVIDER", "anthropic")
    monkeypatch.setenv("POKER_ANTHROPIC_API_KEY", "sk-ant-test123")
    config = load_config(project_root=tmp_path)
    assert config.provider.name == "anthropic"
    assert config.provider.api_key == "sk-ant-test123"


def test_save_and_load_project_config(tmp_path: Path, monkeypatch) -> None:
    """保存后能正确加载项目级配置。"""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("POKER_PROVIDER", raising=False)

    provider = ProviderConfig(name="deepseek", api_key="", model="deepseek-chat", base_url="")
    config = PokerConfig(provider=provider, profile="ci")
    save_project_config(config, tmp_path)

    # 重新加载
    loaded = load_config(project_root=tmp_path)
    assert loaded.provider.name == "deepseek"
    assert loaded.provider.model == "deepseek-chat"
    assert loaded.profile == "ci"


def test_save_and_load_project_config_with_api_key(tmp_path: Path, monkeypatch) -> None:
    """init 输入 API key 后，项目配置可直接用于 REPL。"""
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("POKER_PROVIDER", raising=False)

    provider = ProviderConfig(name="openai", api_key="ak-test", model="test-model", base_url="")
    config = PokerConfig(provider=provider)
    save_project_config(config, tmp_path)

    loaded = load_config(project_root=tmp_path)
    assert loaded.provider.api_key == "ak-test"
    assert loaded.has_api_key is True
