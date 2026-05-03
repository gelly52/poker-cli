"""topic 复杂度分类器：决定 /investigate auto 模式分发到 single 还是 multi agent。

设计目标：
- **极简、稳定**：单轮 LLM 调用，prompt 极短，温度 0，输出限 1 token-ish
- **永不抛栈**：LLM 抛错 / 输出无法识别 / llm=None → 退化 "simple"
- **不依赖 stream_agent_long**：避免循环依赖 + token 浪费
"""
from typing import Any, Literal

from langchain_core.messages import HumanMessage

Verdict = Literal["simple", "complex"]

_PROMPT = (
    "给定下面的安全调查 topic，判断是否需要拆成多个子任务来调查。\n"
    "判定参考：\n"
    "- simple：单 agent 30 步内能搞定（如读单个 prompt、看一个 finding、回答事实问题）\n"
    "- complex：覆盖多个独立维度 / 模块 / 文件类别（如全面评估 prompt injection + RAG + tools 三方面）\n\n"
    "**只输出一个单词，小写：simple 或 complex**。不要解释。\n\n"
    "topic: {topic}"
)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def classify_topic(topic: str, llm: Any) -> Verdict:
    """单轮 LLM 调用判断 topic 复杂度，返回 'simple' | 'complex'。

    任意异常 / 无法识别的输出 / llm=None → 退化 'simple'，绝不抛栈。
    """
    if llm is None or not topic or not topic.strip():
        return "simple"
    prompt = _PROMPT.format(topic=topic.strip())
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception:
        return "simple"
    raw = _content_to_text(getattr(response, "content", response)).strip().lower()
    if not raw:
        return "simple"
    # 只看第一个非空白单词，宽容 "Complex." / "complex - because..." 等
    first = raw.split(None, 1)[0].strip(".,;:!?，。")
    if first.startswith("complex"):
        return "complex"
    if first.startswith("simple"):
        return "simple"
    # 完全不识别 → 默认 simple（保守）
    return "simple"
