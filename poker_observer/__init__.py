"""poker_observer：runtime 观测包，独立于 poker CLI。

用户用法（在自家项目里）：

    from poker_observer import PokerCallbackHandler
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(callbacks=[PokerCallbackHandler(project="my-rag")])

约束：
- 仅依赖 langchain-core
- 钩子最外层 try/except 兜底，永远不抛栈到目标项目
- 写盘异步、本地、不发外网
- opt-in：用户必须显式挂 callback 才生效
- OTel 兼容（可选）：`from poker_observer.otel import to_otel_span`
"""
from poker_observer.detectors import (
    DEFAULT_TOKEN_THRESHOLD,
    detect_in_prompt,
    detect_in_response,
    detect_prompt_injection,
    detect_secret_leak,
    detect_token_anomaly,
)
from poker_observer.handler import PokerCallbackHandler, default_runtime_dir
from poker_observer.otel import to_otel_span
from poker_observer.writer import AsyncJsonlWriter

__all__ = [
    "PokerCallbackHandler",
    "AsyncJsonlWriter",
    "default_runtime_dir",
    "to_otel_span",
    "detect_in_prompt",
    "detect_in_response",
    "detect_prompt_injection",
    "detect_secret_leak",
    "detect_token_anomaly",
    "DEFAULT_TOKEN_THRESHOLD",
]
