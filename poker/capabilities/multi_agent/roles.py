"""多 Agent 协作的 4 个角色实现。

- **Planner**：单轮 LLM，输出子任务 JSON 列表
- **Investigator**：复用 stream_agent_long + capability 工具，per-agent budget=15
- **Critic**：单轮 LLM，**只一轮反馈**，避免无限往复
- **Synthesizer**：单轮 LLM，合并所有产出 + critique 输出最终 markdown

每个角色对外 1 个公开函数；失败都抛 Exception 让编排层 catch。
"""
import json
import re
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from poker.agent.runtime import stream_agent_long
from poker.agent.tools import get_investigate_tools, set_investigation_budget

PER_AGENT_BUDGET = 15
INVESTIGATOR_MAX_ROUNDS = 3


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return str(content)


def _strip_json_fence(text: str) -> str:
    """剥掉 markdown ```json ... ``` 包装。"""
    if not isinstance(text, str):
        return ""
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    return text


# ---------- Planner ----------

_PLANNER_PROMPT = """\
你是安全调查 Planner。把下面 topic 拆成 ≤5 个**独立、可并行**的子任务。

Topic: {topic}

要求：
- 每个子任务范围互斥，不要重复
- 子任务必须是"调查任务"，不是"修复任务"
- 至少 3 个，最多 5 个

**严格只输出 JSON 数组**（不要 markdown 代码块、不要前后说明），结构：
[
  {{"id": "sq1", "goal": "一句话目标", "scope": "限定范围（哪些文件/模块/维度）"}},
  ...
]
"""


def run_planner(topic: str, llm: Any, max_subtasks: int = 5) -> list[dict]:
    """LLM 拆子任务；解析失败 → 兜底返回单条子任务。"""
    if llm is None:
        return [{"id": "sq1", "goal": topic, "scope": ""}]

    prompt = _PLANNER_PROMPT.format(topic=topic)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception:
        return [{"id": "sq1", "goal": topic, "scope": ""}]

    text = _content_to_text(getattr(response, "content", response)).strip()
    body = _strip_json_fence(text)

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # 兜底：取首个 [ 到末尾 ]
        first = body.find("[")
        last = body.rfind("]")
        if first != -1 and last != -1 and last > first:
            try:
                data = json.loads(body[first : last + 1])
            except json.JSONDecodeError:
                return [{"id": "sq1", "goal": topic, "scope": ""}]
        else:
            return [{"id": "sq1", "goal": topic, "scope": ""}]

    if not isinstance(data, list):
        return [{"id": "sq1", "goal": topic, "scope": ""}]

    out: list[dict] = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            continue
        sub_id = str(item.get("id") or f"sq{i}")
        goal = str(item.get("goal") or "").strip()
        scope = str(item.get("scope") or "").strip()
        if not goal:
            continue
        out.append({"id": sub_id, "goal": goal, "scope": scope})
        if len(out) >= max_subtasks:
            break

    return out or [{"id": "sq1", "goal": topic, "scope": ""}]


# ---------- Investigator ----------

INVESTIGATOR_SYSTEM_PROMPT = """\
你是多 Agent 协作中的一个 Investigator，专注完成你被分配到的子任务。

约束：
- 工具调用预算 15 次（capability 工具：run_scan_tool / run_audit_tool / run_trace_tool / read_findings_tool）
- 不要试图覆盖整个 topic —— 那是其他 Investigator 的事
- 最后一轮输出**针对子任务**的 markdown 摘要，引用 finding 用 8 位短 hash ID
"""

_INVESTIGATOR_USER_TMPL = """\
# 总 topic
{topic}

# 你的子任务（{sub_id}）
**目标**：{goal}
**范围**：{scope}

# 输出要求
最后一轮输出 markdown 片段（不要顶级 H1 标题，只用 ## 和 -）：

## 关键发现
- [severity] 摘要 — `<8 位 ID>` @ path:line
（每条限 1 行；如无写"未发现"）

## 简要分析
2-3 段；引用 finding ID 时用反引号

## 针对子任务的修复建议
列点；引用 finding ID
"""


def run_investigator(
    topic: str,
    sub_task: dict,
    project_root: Path,
    llm: Any,
) -> tuple[str, str | None]:
    """单个 investigator 子任务。返回 (markdown, error)；error=None 表示成功。

    使用 thread-local budget，并发安全。
    """
    sub_id = sub_task.get("id", "sq?")
    goal = sub_task.get("goal", "")
    scope = sub_task.get("scope", "")
    user_prompt = _INVESTIGATOR_USER_TMPL.format(
        topic=topic, sub_id=sub_id, goal=goal, scope=scope
    )

    set_investigation_budget(PER_AGENT_BUDGET)
    try:
        tokens: list[str] = []
        for token, _, _round in stream_agent_long(
            llm,
            user_prompt,
            session_id=f"multi-investigator-{sub_id}-{int(time.time() * 1000)}",
            max_rounds=INVESTIGATOR_MAX_ROUNDS,
            tools=get_investigate_tools(),
            system_prompt=INVESTIGATOR_SYSTEM_PROMPT,
        ):
            tokens.append(token)
        return "".join(tokens).strip(), None
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"
    finally:
        set_investigation_budget(0)


# ---------- Critic ----------

_CRITIC_PROMPT = """\
你是 Critic。下面是多个 Investigator 的产出。

**只做一轮反馈**：对每个 Investigator 报告提 2-3 个最关键的问题或质疑（指出遗漏 / 假设错误 / 证据不足）。
不要追加内容，不要继续追问，不要重复 Investigator 已写过的话。

# 总 topic
{topic}

# Investigator 产出
{reports_block}

# 输出格式（markdown，每个 Investigator 一段）
## {sub_id_label}
- 关键问题 1：...
- 关键问题 2：...
- (可选) 关键问题 3：...
"""


def run_critic(topic: str, reports: dict[str, str], llm: Any) -> str:
    """对所有成功的 Investigator 报告做一轮 critique。"""
    if llm is None or not reports:
        return ""
    blocks = []
    for sub_id, md in reports.items():
        blocks.append(f"### {sub_id}\n\n{md.strip()}")
    prompt = _CRITIC_PROMPT.format(
        topic=topic,
        reports_block="\n\n---\n\n".join(blocks),
        sub_id_label="<sub_id>",
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception as e:
        return f"（Critic 调用失败：{type(e).__name__}：{e}）"
    return _content_to_text(getattr(response, "content", response)).strip()


# ---------- Synthesizer ----------

_SYNTHESIZER_PROMPT = """\
你是 Synthesizer。综合多个 Investigator 的产出 + Critic 一轮反馈，给出最终 markdown 报告。

# 总 topic
{topic}

# Investigator 产出（已剔除失败子任务）
{reports_block}

# Critic 反馈
{critique}

# 输出格式（严格遵循）

# 多 Agent 调查：{topic}

## 概述
（一段话总结：调查覆盖了什么、整体风险水位；如有失败 / 截断的子任务请在此说明）

## 关键发现（合并去重）
- [severity] 摘要 — `<8 位 ID>` @ path:line  (来自 sq{{N}})

## 详细分析
### sq1
（来自该 sq 的内容 + 针对 Critic 提出问题的回应或承认局限）

### sq2
...

## 建议
按优先级列出可落地的动作；每条引用 finding ID

要求：
- 引用 finding ID 必须来自 Investigator 报告，不要编造
- 如果 Critic 指出关键问题，请在对应 sq 子节里点出来或说明 gap
"""


def run_synthesizer(
    topic: str,
    reports: dict[str, str],
    critique: str,
    llm: Any,
) -> str:
    """合并所有产出输出最终 markdown。"""
    if llm is None:
        return ""
    blocks = []
    for sub_id, md in reports.items():
        blocks.append(f"### {sub_id}\n\n{md.strip()}")
    prompt = _SYNTHESIZER_PROMPT.format(
        topic=topic,
        reports_block="\n\n---\n\n".join(blocks) if blocks else "（无成功产出）",
        critique=critique.strip() or "（无）",
    )
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception as e:
        return f"# 多 Agent 调查：{topic}\n\n（Synthesizer 调用失败：{type(e).__name__}：{e}）"
    return _content_to_text(getattr(response, "content", response)).strip()
