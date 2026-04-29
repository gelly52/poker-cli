"""Agent 执行循环。

负责 LLM 调用、工具执行、消息管理的核心循环。
不关心具体工具实现和 CLI 交互。
"""

from collections.abc import Generator

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from poker.agent.prompts import SECURITY_AGENT_PROMPT
from poker.agent.tools import get_agent_tools

_HISTORY_STORE: dict[str, InMemoryChatMessageHistory] = {}


def _content_to_text(content: object) -> str:
    """将 LangChain content 统一转成 str，避免 str | list 类型外溢。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    """获取内存会话历史。"""
    if session_id not in _HISTORY_STORE:
        _HISTORY_STORE[session_id] = InMemoryChatMessageHistory()
    return _HISTORY_STORE[session_id]


def create_agent(llm: BaseChatModel, tools: list | None = None):
    """创建一个绑定了工具的 Agent。"""
    agent_tools = tools or get_agent_tools()
    return llm.bind_tools(agent_tools)


def _run_agent_messages(llm: BaseChatModel, messages: list[BaseMessage]) -> AIMessage:
    """对一组消息执行 Agent 工具循环，返回最终 AIMessage。"""
    tools = get_agent_tools()
    tool_map = {t.name: t for t in tools}
    agent = create_agent(llm, tools)
    work_messages: list[BaseMessage] = [SystemMessage(content=SECURITY_AGENT_PROMPT), *messages]

    for _ in range(5):
        response = agent.invoke(work_messages)
        work_messages.append(response)
        tool_calls = getattr(response, "tool_calls", None)

        if not tool_calls:
            return AIMessage(content=_content_to_text(response.content))

        for tc in tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"工具 {tc['name']} 不存在"
            work_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

    return AIMessage(content="工具调用次数过多，请简化问题后重试。")


def create_agent_with_history(llm: BaseChatModel) -> RunnableWithMessageHistory:
    """创建带 RunnableWithMessageHistory 的 Agent。"""
    runnable = RunnableLambda(lambda messages: _run_agent_messages(llm, messages))
    return RunnableWithMessageHistory(runnable, get_session_history)


def run_agent(
    llm: BaseChatModel,
    user_input: str,
    session_id: str = "default",
) -> tuple[str, list]:
    """执行一轮 Agent 交互，返回 (回复内容, 更新后的消息历史)。"""
    agent = create_agent_with_history(llm)
    response = agent.invoke(
        [HumanMessage(content=user_input)],
        config={"configurable": {"session_id": session_id}},
    )
    history = get_session_history(session_id).messages
    return _content_to_text(response.content), history


def stream_agent(
    llm: BaseChatModel,
    user_input: str,
    session_id: str = "default",
) -> Generator[tuple[str, list], None, None]:
    """流式执行一轮 Agent 交互。

    yield (token, messages)，最终一次 yield 的 messages 是完整历史。
    工具调用中间轮：同步执行，yield 状态文本。
    """
    history = get_session_history(session_id)
    human_message = HumanMessage(content=user_input)
    messages: list[BaseMessage] = [SystemMessage(content=SECURITY_AGENT_PROMPT), *history.messages, human_message]

    tools = get_agent_tools()
    tool_map = {t.name: t for t in tools}
    agent = create_agent(llm, tools)

    for _ in range(5):
        collected_chunks = []
        for chunk in agent.stream(messages):
            collected_chunks.append(chunk)
            token = _content_to_text(chunk.content)
            if token:
                yield (token, history.messages)

        response = collected_chunks[0] if collected_chunks else AIMessage(content="")
        for c in collected_chunks[1:]:
            response = response + c
        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None)
        if not tool_calls:
            ai_message = AIMessage(content=_content_to_text(response.content))
            history.add_messages([human_message, ai_message])
            yield ("", history.messages)
            return

        for tc in tool_calls:
            tool_fn = tool_map.get(tc["name"])
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"工具 {tc['name']} 不存在"
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
            yield (f"\n[执行工具: {tc['name']}]\n", history.messages)

    ai_message = AIMessage(content="工具调用次数过多，请简化问题后重试。")
    history.add_messages([human_message, ai_message])
    yield (_content_to_text(ai_message.content), history.messages)
