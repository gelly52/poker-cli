"""scan 命令实现。"""
from pathlib import Path

import typer
from rich.console import Console

from poker.capabilities.scan.engine import scan_path
from poker.capabilities.scan.report import (
    filter_by_mode,
    print_json,
    print_summary,
    print_table_grouped,
    render_json,
    render_markdown,
)
from poker.state import save_findings

console = Console()
SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def register_scan(app: typer.Typer) -> None:
    """将 scan 命令注册到 Typer app。"""

    @app.command()
    def scan(
        target: Path = typer.Argument(Path("."), help="扫描目标：文件或目录"),
        format_: str = typer.Option("table", "--format", "-f", help="输出格式: table, json 或 markdown"),
        output: Path | None = typer.Option(None, "--output", "-o", help="写入报告文件（始终包含全部 findings）"),
        fail_on: str | None = typer.Option(None, "--fail-on", help="达到指定等级时返回非零退出码"),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="只显示 critical / high"),
        verbose: bool = typer.Option(False, "--verbose", "-v", help="显示全部含 info"),
    ) -> None:
        """扫描项目中的 AI 安全风险。"""

        if not target.exists():
            console.print(f"[red]目标不存在:[/red] {target}")
            raise typer.Exit(code=2)

        findings = scan_path(target)
        format_ = format_.lower()

        # 持久化全量 findings（不受 quiet/verbose 影响）
        save_findings(Path.cwd(), findings)

        # 控制台显示用过滤后的子集；--output 写文件用全量
        visible = filter_by_mode(findings, quiet, verbose)

        if output:
            output.write_text(_render_report(format_, findings, target), encoding="utf-8")
            console.print(f"[green]报告已写入:[/green] {output}")
        elif format_ == "table":
            print_table_grouped(console, visible)
            print_summary(console, findings)
        elif format_ == "json":
            print_json(console, visible, target)
        elif format_ == "markdown":
            console.print(render_markdown(visible, target))
        else:
            console.print(f"[red]不支持的格式:[/red] {format_}")
            raise typer.Exit(code=2)

        if _should_fail(findings, fail_on):
            raise typer.Exit(code=1)


def _render_report(format_: str, findings, target: Path) -> str:
    if format_ == "json":
        return render_json(findings, target)
    if format_ == "markdown":
        return render_markdown(findings, target)
    raise typer.BadParameter("--output 仅支持 json 或 markdown 格式")


def _should_fail(findings, fail_on: str | None) -> bool:
    if not fail_on:
        return False
    threshold = SEVERITY_RANK.get(fail_on.lower())
    if threshold is None:
        raise typer.BadParameter(f"不支持的 fail-on 等级: {fail_on}")
    return any(SEVERITY_RANK[f.severity.value] <= threshold for f in findings)
