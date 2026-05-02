"""审计公共类型 + 渲染 helper：Risk / severity ranking / 渲染 / 选择循环。

跨维度共享数据类型，避免 4 份拷贝。维度特定逻辑各自一文件。
"""
from dataclasses import dataclass
from typing import Any, Callable

from rich.console import Console


@dataclass
class Risk:
    """单条风险：检查名 / 等级 / 证据 / 建议。"""
    check: str
    severity: str  # critical | high | medium | low | info
    evidence: str
    recommendation: str


SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
SEVERITY_STYLES = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def overall_severity(risks: list[Risk]) -> str:
    """从 risks 列表中取最严重的等级；空列表返回 info。"""
    overall = "info"
    for r in risks:
        if SEVERITY_RANK[r.severity] < SEVERITY_RANK[overall]:
            overall = r.severity
    return overall


def render_risks_block(
    console: Console, risks: list[Risk], overall: str, llm_summary: str = "",
) -> None:
    """统一渲染 risks + LLM 评估块。"""
    style = SEVERITY_STYLES.get(overall, "white")
    console.print(f"  综合等级: [{style}]{overall.upper()}[/{style}]")
    if not risks:
        console.print("  [green]未发现明显风险[/green]")
    else:
        for r in risks:
            s = SEVERITY_STYLES.get(r.severity, "white")
            console.print(f"  [{s}][{r.severity}][/{s}] {r.check}: {r.evidence}")
            console.print(f"      → {r.recommendation}")
    if llm_summary:
        console.print("\n  [dim]LLM 评估:[/dim]")
        for line in llm_summary.splitlines():
            if line.strip():
                console.print(f"    {line}")


def prompt_selection(
    items: list, label: Callable[[Any], str], console: Console, kind: str,
) -> list | None:
    """通用选择循环：编号 / 名称 / all / quit。返回 None 表示取消。"""
    while True:
        try:
            choice = input(f"选择要审计的 {kind}（编号 / 名称 / all / quit）：").strip()
        except (KeyboardInterrupt, EOFError):
            console.print()
            return None
        if not choice:
            continue
        if choice.startswith("/") or choice.startswith("!"):
            console.print(f"[yellow]当前在审计选择中，先输 'quit' 退出再运行 {choice}[/yellow]")
            continue
        if choice in ("quit", "q", "exit"):
            return None
        if choice == "all":
            return list(items)
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(items):
                return [items[idx]]
            console.print(f"[red]编号超出范围 1..{len(items)}[/red]")
            continue
        matched = [it for it in items if label(it) == choice]
        if matched:
            return matched
        console.print(f"[red]找不到名为 {choice} 的{kind}[/red]")
