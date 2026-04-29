"""Poker CLI command entrypoint."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from poker.reporter import print_json, print_table
from poker.scanner import scan_path

app = typer.Typer(
    name="poker",
    help="Poker CLI - AI security agent CLI for LLM and agent projects.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.callback()
def main() -> None:
    """Poker CLI command group."""


@app.command()
def scan(
    target: Path = typer.Argument(Path("."), help="File or directory to scan."),
    format_: str = typer.Option("table", "--format", "-f", help="Output format: table or json."),
) -> None:
    """Scan a project for early AI security risks."""

    if not target.exists():
        console.print(f"[red]Target does not exist:[/red] {target}")
        raise typer.Exit(code=2)

    findings = scan_path(target)

    if format_ == "table":
        print_table(console, findings)
    elif format_ == "json":
        print_json(console, findings)
    else:
        console.print(f"[red]Unsupported format:[/red] {format_}")
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
