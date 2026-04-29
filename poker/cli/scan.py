"""scan 命令实现。"""
from pathlib import Path

import typer
from rich.console import Console

from poker.capabilities.scan.engine import scan_path
from poker.capabilities.scan.report import print_json, print_table

console = Console()


def register_scan(app: typer.Typer) -> None:
    """将 scan 命令注册到 Typer app。"""

    @app.command()
    def scan(
        target: Path = typer.Argument(Path("."), help="扫描目标：文件或目录"),
        format_: str = typer.Option("table", "--format", "-f", help="输出格式: table 或 json"),
    ) -> None:
        """扫描项目中的 AI 安全风险。"""

        if not target.exists():
            console.print(f"[red]目标不存在:[/red] {target}")
            raise typer.Exit(code=2)

        findings = scan_path(target)

        if format_ == "table":
            print_table(console, findings)
        elif format_ == "json":
            print_json(console, findings)
        else:
            console.print(f"[red]不支持的格式:[/red] {format_}")
            raise typer.Exit(code=2)
