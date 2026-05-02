"""/trace 命令入口。"""
from pathlib import Path

import typer
from rich.console import Console

console = Console()


def register_trace(app: typer.Typer) -> None:
    """将 trace 命令注册到 Typer app。"""

    @app.command()
    def trace(
        target: str = typer.Argument(..., help="<文件:行:变量>"),
    ) -> None:
        """从指定变量追踪数据流，标记是否触达危险 sink。"""
        from poker.agent.tools import set_project_root
        from poker.capabilities.trace import run_trace

        project_root = Path.cwd().resolve()
        set_project_root(project_root)
        run_trace(target, project_root, console)
