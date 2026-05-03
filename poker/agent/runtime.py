"""Agent 执行循环。

负责 LLM 调用、工具执行、消息管理的核心循环。
不关心具体工具实现和 CLI 交互。
"""

import re
import time
from collections.abc import Generator, Iterator

from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.history import RunnableWithMessageHistory

from poker.agent.prompts import REFLECTION_PROMPT, SECURITY_AGENT_PROMPT
from poker.agent.tools import get_agent_tools

_HISTORY_STORE: dict[str, InMemoryChatMessageHistory] = {}
_MAX_LLM_RETRIES = 3
_CONTEXT_LIMIT_HINTS = ("context length", "maximum context", "too long", "context_length_exceeded")
_TOOL_LOOP_LIMIT = 12  # 每个工具调用循环 / plan-execute 轮次内的工具调用上限
_LONG_MAX_ROUNDS = 20  # 长链路 plan-execute-reflect 总轮次上限

_REFLECTION_RE = re.compile(r"<reflection>(.*?)</reflection>", re.DOTALL | re.IGNORECASE)
_STATUS_RE = re.compile(r"status\s*:\s*(\w+)", re.IGNORECASE)
_VALID_REFLECTION_STATUSES = frozenset({"done", "continue", "failed"})

# 工具调用透出限制：参数值 / 结果摘要的截断阈值
_TOOL_ARG_VAL_LIMIT = 50
_TOOL_RESULT_HEAD_LINES = 3
_TOOL_RESULT_MAX_CHARS = 240


def _format_tool_args(args: object) -> str:
    """把工具参数压成单行 `k=v, k=v` 形式；长字符串截断。"""
    if isinstance(args, dict):
        parts: list[str] = []
        for k, v in args.items():
            v_repr = repr(v)
            if len(v_repr) > _TOOL_ARG_VAL_LIMIT + 2:
                v_repr = v_repr[: _TOOL_ARG_VAL_LIMIT] + "…"
            parts.append(f"{k}={v_repr}")
        return ", ".join(parts)
    return repr(args)[: _TOOL_ARG_VAL_LIMIT * 2]


def _format_tool_result(result: object) -> str:
    """工具结果摘要：取前 N 行 + 最大 N 字符；多余部分用 …（还有 X 行） 标记。"""
    text = result if isinstance(result, str) else str(result)
    if not text.strip():
        return "(空)"
    lines = text.splitlines()
    if len(lines) > _TOOL_RESULT_HEAD_LINES:
        head = "\n     ".join(lines[:_TOOL_RESULT_HEAD_LINES])
        return f"{head}\n     …（还有 {len(lines) - _TOOL_RESULT_HEAD_LINES} 行）"
    body = "\n     ".join(lines)
    if len(body) > _TOOL_RESULT_MAX_CHARS:
        body = body[:_TOOL_RESULT_MAX_CHARS] + "…"
    return body


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


def restore_session(session_id: str, records: list[dict]) -> None:
    """把持久化的 chat records 加载到内存 session（覆盖现有内容）。

    records 是 state.load_chat / load_chat_sessions 返回的 dict 形态，
    每条含 role / content。System 消息忽略（runtime 自己管 system prompt）。
    """
    history = get_session_history(session_id)
    history.clear()
    msgs: list[BaseMessage] = []
    for r in records:
        role = r.get("role")
        content = r.get("content", "")
        if role == "user":
            msgs.append(HumanMessage(content=content))
        elif role == "assistant":
            msgs.append(AIMessage(content=content))
    history.add_messages(msgs)


def _stream_with_retry(agent, messages: list[BaseMessage]) -> Iterator:
    """对 agent.stream 包一层重试：仅在尚未 yield 任何 chunk 前重试，避免输出错乱。

    超限错误（context length 等）直接抛 RuntimeError 给上层友好提示，不重试。
    """
    for attempt in range(_MAX_LLM_RETRIES):
        yielded_any = False
        try:
            for chunk in agent.stream(messages):
                yielded_any = True
                yield chunk
            return
        except Exception as e:
            err = str(e).lower()
            if any(h in err for h in _CONTEXT_LIMIT_HINTS):
                raise RuntimeError("上下文已超出模型限制；建议 /resume 切换或开新会话") from e
            if yielded_any or attempt == _MAX_LLM_RETRIES - 1:
                raise
            time.sleep(2**attempt)


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

    for _ in range(_TOOL_LOOP_LIMIT):
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

    def run_with_messages(messages: list[BaseMessage]) -> AIMessage:
        return _run_agent_messages(llm, messages)

    runnable = RunnableLambda(run_with_messages)
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
    messages: list[BaseMessage] = [
        SystemMessage(content=SECURITY_AGENT_PROMPT),
        *history.messages,
        human_message,
    ]

    tools = get_agent_tools()
    tool_map = {t.name: t for t in tools}
    agent = create_agent(llm, tools)

    for _ in range(_TOOL_LOOP_LIMIT):
        collected_chunks = []
        for chunk in _stream_with_retry(agent, messages):
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
            args_preview = _format_tool_args(tc.get("args", {}))
            yield (f"\n▸ {tc['name']}({args_preview})\n", history.messages)
            result = tool_fn.invoke(tc["args"]) if tool_fn else f"工具 {tc['name']} 不存在"
            messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
            yield (f"  └─ {_format_tool_result(result)}\n\n", history.messages)

    ai_message = AIMessage(content="工具调用次数过多，请简化问题后重试。")
    history.add_messages([human_message, ai_message])
    yield (_content_to_text(ai_message.content), history.messages)


# ---------- 长链路：plan-execute-reflect ----------


def _parse_reflection(text: str) -> str | None:
    """从 LLM 输出中提取 <reflection> 块的 status。

    返回 'done' | 'continue' | 'failed'；格式不合法返回 None（视为反思失败）。
    """
    m = _REFLECTION_RE.search(text or "")
    if not m:
        return None
    sm = _STATUS_RE.search(m.group(1))
    if not sm:
        return None
    status = sm.group(1).strip().lower()
    return status if status in _VALID_REFLECTION_STATUSES else None


def _persist_long_history(
    history: InMemoryChatMessageHistory,
    human_message: HumanMessage,
    round_responses: list[str],
) -> None:
    """把多轮的最终响应拼成一条 AIMessage 写入 history。"""
    final_content = "\n\n".join(p for p in round_responses if p)
    history.add_messages([human_message, AIMessage(content=final_content)])


def stream_agent_long(
    llm: BaseChatModel,
    user_input: str,
    session_id: str = "default",
    max_rounds: int = _LONG_MAX_ROUNDS,
    tools: list | None = None,
    system_prompt: str = SECURITY_AGENT_PROMPT,
) -> Generator[tuple[str, list, int], None, None]:
    """plan-execute-reflect 长链路。

    每个 round = 一次 plan-execute（≤12 工具调用）+ 一次反思；
    反思决定是否进入下一轮规划，max_rounds 强制终止。

    yield (token, messages, current_round)；
    最终一次 yield 的 messages 为完整历史。
    单轮 reflection=done 时行为与 stream_agent 等价。

    KeyboardInterrupt：保证已完成轮次的内容落盘后再向上传播。
    反思失败（无 <reflection> / LLM 调用抛错）：直接结束当前轮，不抛栈。

    tools / system_prompt：默认走 chat 配置；调查 / 子能力可注入自己的工具集与 system prompt。
    """
    history = get_session_history(session_id)
    human_message = HumanMessage(content=user_input)
    work_messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        *history.messages,
        human_message,
    ]

    agent_tools = tools if tools is not None else get_agent_tools()
    tool_map = {t.name: t for t in agent_tools}
    agent = create_agent(llm, agent_tools)

    round_responses: list[str] = []
    current_round = 1
    persisted = False

    try:
        while current_round <= max_rounds:
            # ---- plan-execute（最多 _TOOL_LOOP_LIMIT 次工具循环）----
            round_response_text = ""
            for _ in range(_TOOL_LOOP_LIMIT):
                collected_chunks = []
                for chunk in _stream_with_retry(agent, work_messages):
                    collected_chunks.append(chunk)
                    token = _content_to_text(chunk.content)
                    if token:
                        yield (token, history.messages, current_round)

                response = collected_chunks[0] if collected_chunks else AIMessage(content="")
                for c in collected_chunks[1:]:
                    response = response + c
                work_messages.append(response)

                tool_calls = getattr(response, "tool_calls", None)
                if not tool_calls:
                    round_response_text = _content_to_text(response.content)
                    break

                for tc in tool_calls:
                    tool_fn = tool_map.get(tc["name"])
                    args_preview = _format_tool_args(tc.get("args", {}))
                    yield (
                        f"\n▸ {tc['name']}({args_preview})\n",
                        history.messages,
                        current_round,
                    )
                    result = tool_fn.invoke(tc["args"]) if tool_fn else f"工具 {tc['name']} 不存在"
                    work_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
                    yield (
                        f"  └─ {_format_tool_result(result)}\n\n",
                        history.messages,
                        current_round,
                    )

            if round_response_text:
                round_responses.append(round_response_text)

            # ---- 上限检查 ----
            if current_round >= max_rounds:
                yield (
                    f"\n[已达 round 上限 {max_rounds}，强制结束]\n",
                    history.messages,
                    current_round,
                )
                break

            # ---- reflect ----
            try:
                reflection_response = llm.invoke(
                    [*work_messages, HumanMessage(content=REFLECTION_PROMPT)]
                )
                reflection_text = _content_to_text(reflection_response.content)
            except Exception as exc:
                # 反思 LLM 调用失败：UI 提示后结束循环，不抛栈
                yield (
                    f"\n  ─── reflection skipped (llm error: {exc}) ───\n\n",
                    history.messages,
                    current_round,
                )
                break

            status = _parse_reflection(reflection_text)
            status_label = status or "unrecognized"
            # 把反思整段透给 UI（含 status / reason / next_step），让用户能看到模型的判断过程
            yield (
                (
                    f"\n  ─── reflection ({status_label}) ───\n"
                    f"{reflection_text.strip()}\n"
                    f"  ─── end reflection ───\n\n"
                ),
                history.messages,
                current_round,
            )

            if status is None or status in ("done", "failed"):
                # 无合法 reflection 或明确结束：退出
                break

            # ---- continue：进入下一轮 ----
            current_round += 1
            work_messages.append(AIMessage(content=reflection_text))
            work_messages.append(HumanMessage(content="基于上述反思，请继续推进任务。"))
            yield (
                f"\n[Round {current_round} 开始]\n",
                history.messages,
                current_round,
            )

        _persist_long_history(history, human_message, round_responses)
        persisted = True
        yield ("", history.messages, current_round)
    finally:
        if not persisted:
            # KeyboardInterrupt 等异常路径：保证历史落盘一次
            _persist_long_history(history, human_message, round_responses)
