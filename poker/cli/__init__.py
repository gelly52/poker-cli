"""Poker CLI 命令入口。"""
import typer

from poker.cli.config import register_config
from poker.cli.init import register_init
from poker.cli.scan import register_scan

app = typer.Typer(
    name="poker",
    help="Poker CLI - AI security agent CLI for LLM and agent projects.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Poker CLI - AI security agent CLI."""
    if ctx.invoked_subcommand is None:
        from poker.cli.repl import start_repl
        start_repl()


# 注册一次性命令（poker scan / poker init / poker config）
register_scan(app)
register_init(app)
register_config(app)
