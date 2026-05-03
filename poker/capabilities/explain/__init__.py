"""capabilities/explain：用项目上下文解释 finding。

调用方传 finding-id 前缀，本模块负责：
1. 加载最近一次 scan 结果（state.load_last_findings）
2. 短 hash 前缀匹配（无 / 唯一 / 多匹配三态）
3. 唯一匹配 → 构造 prompt 让 LLM 用工具读相关代码 / git，输出针对项目的解释
4. LLM 不可用 / 失败 → 退化到只显示原 finding 的通用建议

风格上跟 capabilities/audit/* 一致：模块直接接收 console，自己负责渲染。
不重写 stream_agent_long；复用 Phase 4-1 的长链路推理。
"""
import hashlib
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from poker.agent.runtime import stream_agent_long
from poker.state import load_last_findings

_ID_LEN = 8
_RECENT_LIMIT = 5


# ---------- 短 hash ID ----------

def compute_finding_id(finding: Any) -> str:
    """从 finding（dict 或 Finding 对象）派生稳定的 8 位短 hash ID。

    基于 (rule_id, path, line, evidence) 计算，确保同一 finding 跨次扫描稳定。
    """
    if hasattr(finding, "to_dict"):
        d = finding.to_dict()
    elif isinstance(finding, dict):
        d = finding
    else:
        raise TypeError(f"unsupported finding type: {type(finding)!r}")
    key = "|".join(str(d.get(k, "")) for k in ("rule_id", "path", "line", "evidence"))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_ID_LEN]


# ---------- prompt ----------

def build_explain_prompt(finding: dict) -> str:
    """生成给 LLM 的 user prompt：要求用工具读项目上下文，输出三段式解释。"""
    return (
        "请用项目上下文解释下面这条安全 finding 在用户代码里具体怎么被触发。"
        "**先调工具读代码，再下结论；不要泛泛复述 CWE/OWASP。**\n\n"
        "# Finding 详情\n"
        f"- 规则 ID: {finding.get('rule_id', '')}\n"
        f"- 严重等级: {finding.get('severity', '')}\n"
        f"- 标题: {finding.get('title', '')}\n"
        f"- 类别: {finding.get('category', '')}\n"
        f"- 位置: {finding.get('path', '')}:{finding.get('line', '')}\n"
        f"- 证据: {finding.get('evidence', '')}\n"
        f"- 通用建议: {finding.get('recommendation', '')}\n\n"
        "# 你需要做的事\n"
        f"1. read_file 读 {finding.get('path', '')} 把 finding 上下文看清楚\n"
        "2. search_code / search_text 找该变量 / 模式的所有引用 / 调用方\n"
        "3. （可选）git_diff / git_status 看是否最近有相关改动\n\n"
        "# 输出结构（中文，仅这三个 section）\n"
        "## 触发路径\n"
        "在哪个函数 / 哪一行被触发，沿调用链一步步说明。\n\n"
        "## 影响范围\n"
        "在本项目里这个 finding 能造成什么具体后果，跟项目本身的功能挂钩。\n\n"
        "## 修复建议（针对本项目）\n"
        "基于上面读到的代码给出可落地的改法，不要泛泛 CWE/OWASP 建议。\n"
    )


# ---------- 主入口 ----------

def explain_finding(
    finding_id: str,
    project_root: Path,
    llm: Any,
    console: Console,
) -> None:
    """主入口：找 finding + 让 LLM 解释 + 渲染。

    - finding_id 为空 / 找不到 → 列最近 _RECENT_LIMIT 条让用户挑
    - 多匹配 → 列候选并提示加长前缀
    - 唯一匹配 + 有 llm → stream_agent_long 跑
    - LLM 失败 → 退化到原 finding 通用建议
    """
    findings = load_last_findings(project_root)
    if not findings:
        console.print(
            "[yellow]还没跑过扫描；先运行 [cyan]/scan <path>[/cyan] 试试[/yellow]"
        )
        return

    if not finding_id:
        console.print("[yellow]/explain 需要 finding-id；最近 5 条:[/yellow]")
        _print_recent(console, findings, _RECENT_LIMIT)
        return

    finding_id_lc = finding_id.lower().strip()
    matches = [f for f in findings if compute_finding_id(f).startswith(finding_id_lc)]

    if not matches:
        console.print(
            f"[yellow]未找到 finding ID 前缀 [cyan]{finding_id}[/cyan]；最近 5 条:[/yellow]"
        )
        _print_recent(console, findings, _RECENT_LIMIT)
        return

    if len(matches) > 1:
        console.print(
            f"[yellow]ID 前缀 [cyan]{finding_id}[/cyan] 匹配 {len(matches)} 条；"
            "请指定更长前缀:[/yellow]"
        )
        _print_findings_table(console, matches)
        return

    finding = matches[0]
    finding_uid = compute_finding_id(finding)
    _print_finding_header(console, finding, finding_uid)

    if llm is None:
        console.print(
            "[yellow]未配置 LLM；无法生成项目相关解释，"
            "下面是原 finding 的通用建议：[/yellow]"
        )
        console.print(f"[dim]{finding.get('recommendation', '')}[/dim]")
        return

    prompt = build_explain_prompt(finding)
    _render_explanation(prompt, llm, console, finding, finding_uid)


# ---------- 渲染辅助 ----------

def _print_recent(console: Console, findings: list[dict], limit: int) -> None:
    _print_findings_table(console, findings[:limit])


def _print_findings_table(console: Console, findings: list[dict]) -> None:
    """输出候选 finding 表，第一列是短 hash ID。"""
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Severity")
    table.add_column("Rule")
    table.add_column("Location")
    table.add_column("Title")
    for f in findings:
        table.add_row(
            compute_finding_id(f),
            str(f.get("severity", "")),
            str(f.get("rule_id", "")),
            f"{f.get('path', '')}:{f.get('line', '')}",
            str(f.get("title", "")),
        )
    console.print(table)


def _print_finding_header(console: Console, finding: dict, finding_uid: str) -> None:
    sev = finding.get("severity", "")
    console.print(
        f"[bold]Finding[/bold] [cyan]{finding_uid}[/cyan]  "
        f"[{sev}] {finding.get('title', '')}"
    )
    console.print(
        f"[dim]{finding.get('path', '')}:{finding.get('line', '')}  ·  "
        f"rule={finding.get('rule_id', '')}[/dim]"
    )
    console.print(f"[dim]证据: {finding.get('evidence', '')}[/dim]\n")


def _render_explanation(
    prompt: str,
    llm: Any,
    console: Console,
    fallback_finding: dict,
    finding_uid: str,
) -> None:
    """跑 stream_agent_long，UI 跟 chat 同款；任何异常退化到 fallback。"""
    text = Text()
    title_base = "Explain"
    try:
        with Live(
            Panel(text, title=title_base, border_style="green"),
            console=console,
            refresh_per_second=8,
        ) as live:
            for token, _, round_idx in stream_agent_long(
                llm, prompt, session_id=f"explain-{finding_uid}"
            ):
                text.append(token)
                title = (
                    f"{title_base} · Round {round_idx}" if round_idx > 1 else title_base
                )
                live.update(Panel(text, title=title, border_style="green"))
    except KeyboardInterrupt:
        console.print("\n[yellow][已中断][/yellow]")
    except Exception as e:
        console.print(
            f"\n[red]LLM 调用失败 ({e})；退化显示原 finding 的通用建议：[/red]"
        )
        console.print(f"[dim]{fallback_finding.get('recommendation', '')}[/dim]")
