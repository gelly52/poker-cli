"""配置数据模型。"""
from dataclasses import dataclass, field


# 支持的 LLM provider
PROVIDERS = ("openai", "anthropic", "deepseek", "qwen", "local")

# 每个 provider 对应的环境变量前缀
PROVIDER_ENV_PREFIX: dict[str, str] = {
    "openai": "OPENAI",
    "anthropic": "ANTHROPIC",
    "deepseek": "DEEPSEEK",
    "qwen": "QWEN",
    "local": "LOCAL",
}


@dataclass
class ProviderConfig:
    """单个 LLM provider 的配置。"""

    name: str = "openai"
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def redacted_key(self) -> str:
        """脱敏展示 API key。"""
        if not self.api_key:
            return "<未设置>"
        if len(self.api_key) <= 8:
            return "***"
        return f"{self.api_key[:3]}***{self.api_key[-3:]}"


@dataclass
class PokerConfig:
    """Poker CLI 全局配置。"""

    provider: ProviderConfig = field(default_factory=ProviderConfig)
    profile: str = "default"

    @property
    def has_api_key(self) -> bool:
        """是否具备调用 LLM 的最低条件（有 API key）。"""
        return bool(self.provider.api_key)
