"""配置加载与合并。

优先级（高→低）：环境变量 > 项目级配置 > 用户级配置 > 默认值
"""
import os
from pathlib import Path

from poker.config.models import PokerConfig, ProviderConfig, PROVIDER_ENV_PREFIX, PROVIDERS

# 配置文件路径常量
PROJECT_CONFIG_DIR = ".aisec"
PROJECT_CONFIG_FILE = "config.toml"
USER_CONFIG_DIR = ".aisec"


def _user_config_path() -> Path:
    """用户级配置路径：~/.aisec/config.toml"""
    return Path.home() / USER_CONFIG_DIR / PROJECT_CONFIG_FILE


def _project_config_path(project_root: Path | None = None) -> Path:
    """项目级配置路径：<project>/.aisec/config.toml"""
    root = project_root or Path.cwd()
    return root / PROJECT_CONFIG_DIR / PROJECT_CONFIG_FILE


def _load_toml(path: Path) -> dict:
    """简易 TOML 读取（仅支持单层键值对，避免引入新依赖）。"""
    if not path.exists():
        return {}
    data: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def _merge_provider_config(
    provider_name: str,
    user_data: dict,
    project_data: dict,
) -> ProviderConfig:
    """从多个来源合并单个 provider 配置。"""
    env_prefix = PROVIDER_ENV_PREFIX.get(provider_name, "OPENAI")
    defaults = {
        "name": provider_name,
        "api_key": "",
        "base_url": "",
        "model": "",
    }

    # 依次覆盖：默认 → 用户级 → 项目级 → 环境变量
    for source in (user_data, project_data):
        for key in defaults:
            val = source.get(f"provider.{key}", "")
            if val:
                defaults[key] = val

    # 环境变量最高优先级
    env_key = os.environ.get(f"POKER_{env_prefix}_API_KEY", "")
    if env_key:
        defaults["api_key"] = env_key
    env_url = os.environ.get(f"POKER_{env_prefix}_BASE_URL", "")
    if env_url:
        defaults["base_url"] = env_url
    env_model = os.environ.get(f"POKER_{env_prefix}_MODEL", "")
    if env_model:
        defaults["model"] = env_model

    # 兼容旧版 .env 格式
    if not defaults["api_key"]:
        defaults["api_key"] = os.environ.get("API_KEY", "")
    if not defaults["base_url"]:
        defaults["base_url"] = os.environ.get("BASE_URL", "")
    if not defaults["model"]:
        defaults["model"] = os.environ.get("MODEL_NAME", "")

    return ProviderConfig(**defaults)


def load_config(project_root: Path | None = None) -> PokerConfig:
    """加载并合并配置。惰性加载——调用时才读取，import 阶段不触发。"""
    user_data = _load_toml(_user_config_path())
    project_data = _load_toml(_project_config_path(project_root))

    # 确定当前 provider
    provider_name = (
        os.environ.get("POKER_PROVIDER")
        or project_data.get("provider.name", "")
        or user_data.get("provider.name", "")
        or "openai"
    )
    if provider_name not in PROVIDERS:
        provider_name = "openai"

    provider = _merge_provider_config(provider_name, user_data, project_data)
    profile = (
        os.environ.get("POKER_PROFILE")
        or project_data.get("profile", "")
        or user_data.get("profile", "")
        or "default"
    )

    return PokerConfig(provider=provider, profile=profile)


def save_project_config(config: PokerConfig, project_root: Path) -> None:
    """保存项目级配置文件。"""
    config_dir = project_root / PROJECT_CONFIG_DIR
    config_dir.mkdir(exist_ok=True)
    config_path = config_dir / PROJECT_CONFIG_FILE

    lines = [
        "# Poker CLI 项目配置",
        f"provider.name = \"{config.provider.name}\"",
        f"provider.model = \"{config.provider.model}\"",
        f"provider.base_url = \"{config.provider.base_url}\"",
    ]
    if config.provider.api_key:
        lines.append(f"provider.api_key = \"{config.provider.api_key}\"")
    lines.append(f"profile = \"{config.profile}\"")
    config_path.write_text("\n".join(lines), encoding="utf-8")
