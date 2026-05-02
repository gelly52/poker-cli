"""扫描报告渲染。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from poker.models import Finding

SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"]
_SEVERITY_STYLES = {
    "critical": "bold red",
    "high": "red",
    "medium": "yellow",
    "low": "blue",
    "info": "dim",
}


def _payload(findings: list[Finding], target: Path | None = None) -> dict[str, object]:
    return {
        "tool": "poker-cli",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": str(target) if target else None,
        "summary": {"total": len(findings)},
        "findings": [finding.to_dict() for finding in findings],
    }


def render_json(findings: list[Finding], target: Path | None = None) -> str:
    """生成稳定 JSON 报告文本。"""
    return json.dumps(_payload(findings, target), ensure_ascii=False, indent=2)


def render_markdown(findings: list[Finding], target: Path | None = None) -> str:
    """生成 Markdown 报告文本。"""
    lines = ["# Poker CLI Security Report", ""]
    if target:
        lines += [f"- Target: `{target}`"]
    lines += [f"- Findings: {len(findings)}", "", "## Findings", ""]
    if not findings:
        return "\n".join(lines + ["No findings detected.", ""])
    for finding in findings:
        lines += [
            f"### [{finding.severity.value}] {finding.title}",
            "",
            f"- Rule: `{finding.rule_id}`",
            f"- Category: `{finding.category}`",
            f"- Location: `{finding.path}:{finding.line}`",
            f"- Evidence: `{finding.evidence}`",
            f"- Recommendation: {finding.recommendation}",
            "",
        ]
    return "\n".join(lines)


def print_table(console: Console, findings: list[Finding]) -> None:
    """将扫描结果渲染为单表（兼容旧调用，新代码请用 print_table_grouped）。"""
    if not findings:
        console.print("[green]No findings detected.[/green]")
        return

    table = Table(title="Poker CLI Security Findings")
    table.add_column("Severity", style="bold")
    table.add_column("Rule")
    table.add_column("Location")
    table.add_column("Finding")

    for finding in findings:
        table.add_row(
            finding.severity.value,
            finding.rule_id,
            f"{finding.path}:{finding.line}",
            finding.title,
        )
    console.print(table)


def print_table_grouped(console: Console, findings: list[Finding]) -> None:
    """按 severity 分组渲染：每个等级一张子表 + 颜色区分。"""
    if not findings:
        console.print("[green]No findings detected.[/green]")
        return

    grouped: dict[str, list[Finding]] = {sev: [] for sev in SEVERITY_ORDER}
    for f in findings:
        grouped.setdefault(f.severity.value, []).append(f)

    first = True
    for sev in SEVERITY_ORDER:
        items = grouped.get(sev, [])
        if not items:
            continue
        style = _SEVERITY_STYLES[sev]
        if not first:
            console.print()
        first = False
        console.print(f"[{style}]== {sev.upper()} ({len(items)}) ==[/{style}]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Rule", style="bold")
        table.add_column("Location")
        table.add_column("Finding")
        for f in items:
            table.add_row(f.rule_id, f"{f.path}:{f.line}", f.title)
        console.print(table)


def print_summary(console: Console, findings: list[Finding]) -> None:
    """一行总览：critical=N | high=N | medium=N | low=N | info=N。"""
    counts = {sev: 0 for sev in SEVERITY_ORDER}
    for f in findings:
        counts[f.severity.value] = counts.get(f.severity.value, 0) + 1

    parts: list[str] = []
    for sev in SEVERITY_ORDER:
        n = counts[sev]
        if n == 0:
            continue
        style = _SEVERITY_STYLES[sev]
        parts.append(f"[{style}]{sev}={n}[/{style}]")
    summary_line = " | ".join(parts) if parts else "[green]no findings[/green]"
    console.print(f"\n总览: {summary_line} （共 {len(findings)} 条）")


def filter_by_mode(findings: list[Finding], quiet: bool, verbose: bool) -> list[Finding]:
    """根据 quiet/verbose 模式过滤显示集。

    - quiet:   只保留 critical / high
    - verbose: 全部（含 info）
    - 默认:    排除 info
    quiet 和 verbose 同时开启时以 verbose 为准。
    """
    if verbose:
        return findings
    if quiet:
        return [f for f in findings if f.severity.value in ("critical", "high")]
    return [f for f in findings if f.severity.value != "info"]


def print_json(console: Console, findings: list[Finding], target: Path | None = None) -> None:
    """将扫描结果渲染为 JSON。"""
    console.print(render_json(findings, target))
