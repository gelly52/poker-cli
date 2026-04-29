"""ChatModel 工厂：根据配置创建对应的 LLM 实例。"""
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from poker.config.models import ProviderConfig

# OpenAI-compatible provider 统一使用 ChatOpenAI
OPENAI_COMPATIBLE = ("openai", "deepseek", "qwen", "local")


def create_chat_model(config: ProviderConfig) -> BaseChatModel:
    """根据 provider 配置创建 ChatModel 实例。"""
    if config.name in OPENAI_COMPATIBLE:
        return _create_openai_compatible(config)

    # Anthropic 等其他 provider 在此处扩展
    return _create_openai_compatible(config)


def _create_openai_compatible(config: ProviderConfig) -> ChatOpenAI:
    """创建 OpenAI-compatible ChatModel（覆盖 openai/deepseek/qwen/local）。"""
    kwargs: dict = {
        "api_key": config.api_key or "placeholder",
        "model": config.model or "gpt-4o-mini",
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    elif config.name == "deepseek":
        kwargs["base_url"] = "https://api.deepseek.com"
    elif config.name == "qwen":
        kwargs["base_url"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    return ChatOpenAI(**kwargs)
