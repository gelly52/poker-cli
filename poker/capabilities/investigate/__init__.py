"""capabilities/investigate：长链路安全调查 + markdown 报告。

主入口 `run_investigation`：
1. 启动调查模式（设 capability 工具预算 = 30）
2. 用 INVESTIGATE_PROMPT 喂给 `runtime.stream_agent_long`，注入 `get_investigate_tools()`
3. 实时渲染 Live Panel；Ctrl+C / 异常都会把已生成的 markdown 落盘
4. 调用结束（无论正常 / 中断 / 失败）一定会关闭调查模式 + 落盘
"""
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from poker.agent.runtime import stream_agent_long
from poker.agent.tools import (
    get_investigate_tools,
    investigation_tool_usage,
    set_investigation_budget,
)
from poker.state import save_investigation

_TOOL_BUDGET = 30
_MAX_ROUNDS = 8  # 上限 8 轮 × 每轮 5 工具 = 40 名义上限；预算 30 才是真正硬约束


INVESTIGATE_SYSTEM_PROMPT = """\
你是 Poker CLI 的安全调查 Agent。

工作模式：
1. 用户给主题，你自主规划工具调用，深度调查相关安全风险
2. 调查工具预算 30 次（capability 工具：run_scan_tool / run_audit_tool / run_trace_tool / read_findings_tool）
3. 读项目工具不计入预算（read_file / list_files / search_text / search_code / git_diff / git_status）
4. 工具结果会被精简后回喂给你，请基于摘要继续推理；如需细节用 read_file 单点深入
5. 最后一轮输出 markdown 报告（不再调工具），引用 finding 时用 8 位短 hash ID

风险按严重等级排列：critical > high > medium > low > info；不确定的明确标注。\
"""


def _build_user_prompt(topic: str) -> str:
    return f"""\
# 调查主题
{topic}

# 你需要做的事
1. 先用 list_files / search_code / read_file 探查与本主题相关的文件、配置、入口点
2. 视情况调 run_scan_tool（默认整项目扫）/ run_audit_tool（dimension ∈ tools/rag/mcp/prompt）/ run_trace_tool 收集证据
3. 必要时用 read_findings_tool 看完整 finding 列表
4. 综合所有证据，最后一轮**只输出 markdown 报告**（不再调任何工具）

# 报告 markdown 格式（严格遵循）
# 安全调查：{topic}

## 目录
- [背景与范围](#背景与范围)
- [关键发现](#关键发现)
- [详细分析](#详细分析)
- [修复建议](#修复建议)

## 背景与范围
（用一段话说明本次调查的目标、覆盖文件 / 模块、采用的工具）

## 关键发现
按严重程度从高到低排列。每行格式：
- **[severity]** 一句话摘要 — finding `<8 位 ID>` @ path:line

## 详细分析
为每条关键发现起一个二级小节（### 标题），描述：
- 触发路径（沿调用 / 数据流一步步说明）
- 影响（针对本项目的具体后果）
- 证据（引用文件 / 代码片段）

## 修复建议
按优先级列出可落地的修复动作；每条尽量绑定具体 finding ID。

注意：
- 关键发现引用 finding 时必须用 8 位短 hash ID（来自 run_scan_tool / read_findings_tool 的输出）
- 报告里不要再列调试中读过的所有文件，只放结论性证据
- 不要在最后一轮再调任何工具，专心写报告
"""


def run_investigation(
    topic: str,
    project_root: Path,
    llm: Any,
    console: Console,
) -> Path | None:
    """主入口：跑长链路调查 + 落盘 markdown 报告。返回报告路径或 None。"""
    topic = (topic or "").strip()
    if not topic:
        console.print(
            "[yellow]/investigate 需要主题；例：/investigate \"prompt injection 抗性\"[/yellow]"
        )
        return None
    if llm is None:
        console.print("[red]未配置 LLM；/investigate 需要 API key[/red]")
        return None

    user_prompt = _build_user_prompt(topic)
    text = Text()
    title_base = "Investigate"
    interrupted = False
    error: Exception | None = None
    session_id = f"investigate-{int(time.time())}"

    set_investigation_budget(_TOOL_BUDGET)
    try:
        with Live(
            Panel(text, title=title_base, border_style="cyan"),
            console=console,
            refresh_per_second=8,
        ) as live:
            for token, _, round_idx in stream_agent_long(
                llm,
                user_prompt,
                session_id=session_id,
                max_rounds=_MAX_ROUNDS,
                tools=get_investigate_tools(),
                system_prompt=INVESTIGATE_SYSTEM_PROMPT,
            ):
                text.append(token)
                used, total = investigation_tool_usage()
                title = (
                    f"{title_base} · Round {round_idx} · 工具 {used}/{total}"
                    if total > 0
                    else f"{title_base} · Round {round_idx}"
                )
                live.update(Panel(text, title=title, border_style="cyan"))
    except KeyboardInterrupt:
        interrupted = True
    except Exception as e:
        error = e
    finally:
        used, total = investigation_tool_usage()
        set_investigation_budget(0)

    report_md = str(text).strip()

    if interrupted:
        console.print(
            f"\n[yellow][已中断]  工具消耗 {used}/{total}；已生成部分将落盘[/yellow]"
        )
    elif error is not None:
        console.print(
            f"\n[red]调查异常 ({error})；已生成部分将落盘[/red]"
        )
    else:
        console.print(f"\n[dim]调查完成，工具消耗 {used}/{total}[/dim]")

    if not report_md:
        console.print("[yellow]未生成任何报告内容；不落盘[/yellow]")
        return None

    try:
        path = save_investigation(project_root, topic, report_md)
    except Exception as e:
        console.print(f"[red]报告落盘失败: {e}[/red]")
        return None

    console.print(f"[green]报告已落盘:[/green] [cyan]{path}[/cyan]")
    return path
