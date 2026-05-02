"""/redteam 命令入口。"""
from pathlib import Path

import typer
from rich.console import Console

console = Console()


def register_redteam(app: typer.Typer) -> None:
    """将 redteam 命令注册到 Typer app。"""

    @app.command()
    def redteam(
        prompt_file: Path = typer.Argument(..., help="prompt 文件路径"),
    ) -> None:
        """对 prompt 文件生成攻击载荷（不实际执行 endpoint）。"""
        from poker.agent.tools import set_project_root
        from poker.capabilities.redteam import run_redteam

        project_root = Path.cwd().resolve()
        set_project_root(project_root)
        run_redteam(prompt_file, project_root, console)
