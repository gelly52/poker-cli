"""/audit 命令入口。"""
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def register_audit(app: typer.Typer) -> None:
    """将 audit 命令注册到 Typer app。"""

    @app.command()
    def audit(
        dimension: str = typer.Argument("tools", help="审计维度：tools / rag / mcp / prompt / mcp_schema"),
        schema: Optional[Path] = typer.Option(
            None, "--schema",
            help="MCP tools/list schema 文件（mcp_schema 维度专用）",
        ),
    ) -> None:
        """深度审计某个安全维度，多步交互式。"""
        from poker.agent.llm import create_chat_model
        from poker.agent.tools import set_project_root
        from poker.capabilities.audit import run_audit
        from poker.config import load_config

        project_root = Path.cwd().resolve()
        set_project_root(project_root)

        config = load_config()
        llm = create_chat_model(config.provider) if config.has_api_key else None

        try:
            run_audit(dimension, project_root, llm, console, schema_path=schema)
        except NotImplementedError as e:
            console.print(f"[yellow]{e}[/yellow]")
            raise typer.Exit(code=2)
