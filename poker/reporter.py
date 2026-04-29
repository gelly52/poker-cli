"""Report rendering helpers."""

from __future__ import annotations

import json

from rich.console import Console
from rich.table import Table

from poker.models import Finding


def print_table(console: Console, findings: list[Finding]) -> None:
    """Render findings as a compact terminal table."""

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


def print_json(console: Console, findings: list[Finding]) -> None:
    """Render findings as JSON."""

    console.print(
        json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False, indent=2)
    )
