"""扫描报告渲染。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from poker.models import Finding


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
    """将扫描结果渲染为终端表格。"""

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


def print_json(console: Console, findings: list[Finding], target: Path | None = None) -> None:
    """将扫描结果渲染为 JSON。"""
    console.print(render_json(findings, target))

