"""Tests for /investigate auto-mode classifier."""
import pytest
from langchain_core.messages import AIMessage

from poker.capabilities.multi_agent.classifier import classify_topic


class _ScriptedLLM:
    def __init__(self, output: str) -> None:
        self.output = output
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content=self.output)


# ---------- 简单 / 复杂 识别 ----------

def test_classify_simple_lowercase() -> None:
    assert classify_topic("看下 README", _ScriptedLLM("simple")) == "simple"


def test_classify_complex_lowercase() -> None:
    assert classify_topic("全面安全分析", _ScriptedLLM("complex")) == "complex"


def test_classify_capitalized_complex() -> None:
    assert classify_topic("topic", _ScriptedLLM("Complex.")) == "complex"


def test_classify_complex_with_explanation() -> None:
    """模型多嘴解释也应识别 —— 我们看第一个单词。"""
    assert (
        classify_topic("topic", _ScriptedLLM("complex - because it covers 3 dimensions"))
        == "complex"
    )


def test_classify_simple_with_punctuation() -> None:
    assert classify_topic("topic", _ScriptedLLM("Simple,")) == "simple"


def test_classify_chinese_punct_stripped() -> None:
    assert classify_topic("topic", _ScriptedLLM("simple。")) == "simple"


# ---------- fallback 路径 ----------

def test_classify_falls_back_to_simple_on_llm_exception() -> None:
    class _BoomLLM:
        def invoke(self, _msgs):
            raise RuntimeError("network down")

    assert classify_topic("topic", _BoomLLM()) == "simple"


def test_classify_unknown_output_falls_back_simple() -> None:
    assert classify_topic("topic", _ScriptedLLM("maybe?")) == "simple"


def test_classify_empty_output_falls_back_simple() -> None:
    assert classify_topic("topic", _ScriptedLLM("")) == "simple"
    assert classify_topic("topic", _ScriptedLLM("   ")) == "simple"


def test_classify_none_llm_returns_simple() -> None:
    assert classify_topic("topic", None) == "simple"


def test_classify_empty_topic_returns_simple_without_llm_call() -> None:
    llm = _ScriptedLLM("complex")
    assert classify_topic("", llm) == "simple"
    assert classify_topic("   ", llm) == "simple"
    assert llm.calls == 0  # 空 topic 跳过 LLM 调用


def test_classify_actual_prompt_sent_to_llm() -> None:
    """LLM 收到的 prompt 应当包含 topic + 简单 / 复杂判定参考。"""
    received: dict = {}

    class _CapturingLLM:
        def invoke(self, messages):
            received["content"] = messages[0].content
            return AIMessage(content="simple")

    classify_topic("帮我看下 prompt 文件有没有问题", _CapturingLLM())
    assert "帮我看下 prompt 文件有没有问题" in received["content"]
    assert "simple" in received["content"].lower()
    assert "complex" in received["content"].lower()


def test_classify_robust_to_non_string_content() -> None:
    """LangChain 偶尔返回 list 形式 content；不应抛栈。"""

    class _ListContentLLM:
        def invoke(self, _msgs):
            class _Resp:
                content = ["complex"]

            return _Resp()

    # str(["complex"]) -> "['complex']"，首单词 "['complex']" 含 'complex' 但前缀不是 'complex'
    # → 退化 simple；只要不抛栈就过
    result = classify_topic("topic", _ListContentLLM())
    assert result in ("simple", "complex")
